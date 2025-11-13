LevelDB – Automated Harness Generation & Bug Discovery Workflow
==============================================================

This document is a case-study of the **end-to-end workflow** that the
LLM-powered harness generator followed to find, trigger and triage a
previously-undiscovered bug in "leveldb".

Contents
--------
1.  High-level timeline
2.  Environment bootstrap
3.  How the new target was chosen
4.  Codex harness synthesis (`fuzz_table_open.cc`)
5.  Building & running the new target
6.  Crash detection & reproduction
7.  Automated crash triage and false positive detection (→ `crash_analysis.md`)
8.  Resulting artifacts
9.  Harness quality & model intentionality 
10. Key take-aways

## 1. High-level timeline

| Step | Actor | What happened |
|------|-------|---------------|
| 1 | *cli wrapper* | Clone LevelDB OSS-Fuzz project and build the **baseline fuzzers** (only `fuzz_db`). |
| 2 | *Codex* | Prompted with high-level instructions to “add one **new** fuzz target that reaches previously unfuzzed code”. |
| 3 | *builder* | Codex edits the tree, creating `projects/leveldb/fuzz_table_open.cc` and adjusting build scripts. |
| 4 | *fuzzer* | libFuzzer starts; within seconds hits an **OOM in `ReadBlock()`**. |
| 5 | *runner* | Detects a new `oom-*` file, reproduces the issue and copies the logs →  `crash_info.md`. |
| 6 | *Codex* | Second prompt: *“Analyse this stack trace & produce human report.”*  Output stored in `crash_analysis.md`. |


## 2. Environment bootstrap

The harness generator launches the standard OSS-Fuzz helper scripts:

```text
$ python infra/helper.py build_image leveldb
$ python infra/helper.py build_fuzzers leveldb --sanitizer address --clean
```

The log excerpt below shows that **only one
baseline target** was discovered:

```text
[*] Baseline has 1 fuzzer(s): { 'fuzz_db' }
```

`fuzz_db` exercises the public database API with randomly generated keys
and values, but *never loads SSTable files from disk* – a gap our
analysis will soon exploit.


## 3. How the new target was chosen

After the baseline build, the workflow jumps straight to a Codex
invocation that is given high-level instructions to create a new fuzzer
harness for the project.  
(see [`harness_generator.py`](../harness_generator/src/harness_generator.py) → `_invoke_codex_for_harness`)

Codex is free to inspect any file in the working copy, clone the main
repository, or rely on its own training data.  The **selection logic is
therefore internal to the LLM** – the Python driver makes *no* attempt
to parse ELF symbol tables, ASTs, or code coverage reports.

For LevelDB, Codex picked
```c++
Status Table::Open(const Options&, RandomAccessFile*, uint64_t file_size,
                   Table**);
```

from `table/table.cc`.  This function parses on-disk SSTable files and
was not reached by the existing `fuzz_db` target, making it a sensible
choice even without a pre-computed coverage map.


## 4. Codex harness synthesis

### Codex instructions (excerpt)

```text
**Objective (high-value fuzz target)**  
Create a **new libFuzzer harness** for the **leveldb** OSS-Fuzz project that
exercises a *public* or *documented* API reachable with **user-supplied input**
(e.g. files, packets, strings) and therefore has real-world security impact.

────────────────────────────────────────
**Target-selection rules**

1. **Start at the top**: pick the *highest-level* function that
*directly* consumes attacker-controlled data.  
• Good examples: `exif_data_load()`, `freerdp_peer_context_new()`,  
  `curl_url_set()`, `png_read_info()`.  
• **Avoid** low-level helpers (`*_parse_int()`, `*_read_field()` etc.)
  unless *no higher layer* validates input.
```

### What Codex does

1. Analyzes `projects/leveldb/` to learn how LevelDB objects are
   initialised, how the corpus input is written to disk, and how the
   harness is compiled (compiler flags, build.sh edits, etc.).
2. Generates `projects/leveldb/fuzz_table_open.cc`, re-using the helper
   functions and error handling patterns it saw in `fuzz_db.cc`.
3. Opens `projects/leveldb/build.sh` (or `Dockerfile` / `CMakeLists.txt`
   depending on the project) and appends a single `compile_cc` line so
   the new `.cc` file is compiled into a `fuzz_table_open` binary next
   to the existing `fuzz_db` target.
4. Writes the path of the new harness to a sentinel file called `./done`
   so the Python driver knows that edits are complete.

The produced harness is short enough to show in full:

```c++
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  const char *fname = "/tmp/fuzz_table_open.sst";
  std::ofstream out(fname, std::ios::binary);
  if (!out) return 0;
  out.write(reinterpret_cast<const char *>(data), size);

  leveldb::Options options;
  options.env = leveldb::Env::Default();

  leveldb::RandomAccessFile *file = nullptr;
  if (!options.env->NewRandomAccessFile(fname, &file).ok()) return 0;

  leveldb::Table *table = nullptr;
  if (!leveldb::Table::Open(options, file, size, &table).ok()) {
    delete file;
    return 0;
  }

  std::unique_ptr<leveldb::Iterator> it(
      table->NewIterator(leveldb::ReadOptions()));
  for (it->SeekToFirst(); it->Valid(); it->Next()) {
    /* no-op – iteration alone is enough to exercise the parser */
  }

  delete table;
  delete file;
  std::remove(fname);
  return 0;
}
```

### Build-until-green loop

The driver now calls `_build_with_retries()` which attempts to rebuild
the project.  If compilation fails, the captured stderr is forwarded to
`_invoke_codex_to_fix_build`, and Codex applies the smallest possible
patch (typically adding a missing header include or fixing a compiler
flag).  This loop repeats until the build succeeds or the retry budget
is exhausted.  For LevelDB the very first build already succeeded
because the harness was modelled closely after `fuzz_db.cc`.


## 5.  Building & running the new target

After compilation the runner automatically executed a short sanity fuzz
session (1 k coverage-guided iterations).  Within ~2000 execs libFuzzer
emitted:

```text
==14==ERROR: AddressSanitizer: out of memory: allocator is trying to
allocate 0xffffffffffe0 bytes
    #0 0x... in operator new[](unsigned long)
    #1 0x... in leveldb::ReadBlock(...)
    #2 0x... in leveldb::Table::Open(...)
```

Because the crash originates **inside project code (`format.cc:78`)** and
the requested allocation is clearly bogus (`0xffffffffffe0`), the signal
is marked as a *real* bug and saved.  Reproducer and log were copied to
`build/out/leveldb/crash-<SHA>`.


## 6.  Crash detection & reproduction

After the fuzzer exits, `harness_generator.py` walks the
`build/out/leveldb` directory tree and records every file whose name
begins with one of the libFuzzer prefixes `crash`, `oom`, or `timeout`
(see `_find_bug_files`).  Any *new* file is assumed to be a genuine
finding.  The very first one is reproduced with the standard
`infra/helper.py reproduce` command and its artifacts are collected into
`crash_info.md`.


## 7.  Automated crash triage

A second Codex invocation is fed the **raw ASan log plus the offending
source lines** to produce a human-readable crash analysis.

Codex produced `crash_analysis.md`, identifying the issue as *unbounded
memory allocation due to unchecked block handle size* and suggesting to
validate `offset + size` before allocation – exactly the fix a human
would write.


## 8.  Resulting artifacts

All important files live under `leveldb_writeup/artifacts/`:

* `crash_info.md` – Reproducer command, ASan log, harness snippet & hex-dump.
* `crash_analysis.md` – High-level vulnerability assessment.


## 9. Harness quality & model intentionality

On the **first attempt**, Codex (o3) selected `leveldb::Table::Open()`, a
high-level, attacker-controlled file parser that the baseline target never
touched, produced a compiling harness without retries, and triggered an OOM in
`ReadBlock()` within \~2k execs. This happened without any external coverage,
static analysis, or symbol/AST reasoning.

That outcome indicates the model is not merely emitting syntactically correct
code; it is **prioritizing code patterns that historically harbor bugs**
(complex, input-driven parsers). This reflects a learned, security-relevant
**inductive prior**. In practice, this displaces a large portion of the manual
front-end work (enumerating and ranking candidate entry points, drafting an
initial harness, and iterating to green): the model did that prioritization and
delivered a crash-inducing target in a single pass.


## 10. Key take-aways

1. Even mature OSS-Fuzz projects often have *format-parsing* code paths
   left unfuzzed – here the SSTable reader.
2. Even without an external coverage map, a single well-crafted prompt
   is often enough for the LLM to identify an unfuzzed API and deliver a
   working harness on the first attempt.
3. The same LLM that wrote the harness can immediately explain the bug –
   closing the loop from discovery to actionable triage with no human in
   the middle.
4. Codex (o3) showed a learned bias toward high-risk parsing entry points,
   producing a crash-inducing harness on the first try without coverage or
   static guidance — evidence that LLMs can assume much of the early triage
   and target-selection workload with surprising precision.
