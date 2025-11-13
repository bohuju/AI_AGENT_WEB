# Crash Info

## Reproducer command
```bash
python infra/helper.py reproduce leveldb fuzz_table_open build/out/leveldb/crash-eb318a4efc67ba9452a00fc1e8bec0fd4bc8ecd3
```

## Reproducer log
```text
# python infra/helper.py reproduce leveldb fuzz_table_open build/out/leveldb/crash-eb318a4efc67ba9452a00fc1e8bec0fd4bc8ecd3
+ FUZZER=fuzz_table_open
+ shift
+ '[' '!' -v TESTCASE ']'
+ TESTCASE=/testcase
+ '[' '!' -f /testcase ']'
+ export RUN_FUZZER_MODE=interactive
+ RUN_FUZZER_MODE=interactive
+ export FUZZING_ENGINE=libfuzzer
+ FUZZING_ENGINE=libfuzzer
+ export SKIP_SEED_CORPUS=1
+ SKIP_SEED_CORPUS=1
+ run_fuzzer fuzz_table_open -runs=100 /testcase
vm.mmap_rnd_bits = 28
/out/fuzz_table_open -rss_limit_mb=2560 -timeout=25 -runs=100 /testcase < /dev/null
INFO: Running with entropic power schedule (0xFF, 100).
INFO: Seed: 1983861041
INFO: Loaded 1 modules   (1554 inline 8-bit counters): 1554 [0x5591fc773288, 0x5591fc77389a), 
INFO: Loaded 1 PC tables (1554 PCs): 1554 [0x5591fc7738a0,0x5591fc7799c0), 
/out/fuzz_table_open: Running 1 inputs 100 time(s) each.
Running: /testcase
==14==WARNING: AddressSanitizer failed to allocate 0xffffffffffe0 bytes
=================================================================
==14==ERROR: AddressSanitizer: out of memory: allocator is trying to allocate 0xffffffffffe0 bytes
    #0 0x5591fc64a44d in operator new[](unsigned long) /src/llvm-project/compiler-rt/lib/asan/asan_new_delete.cpp:89:3
    #1 0x5591fc66bac0 in leveldb::ReadBlock(leveldb::RandomAccessFile*, leveldb::ReadOptions const&, leveldb::BlockHandle const&, leveldb::BlockContents*) /src/leveldb/table/format.cc:78:15
    #2 0x5591fc64d960 in leveldb::Table::Open(leveldb::Options const&, leveldb::RandomAccessFile*, unsigned long, leveldb::Table**) /src/leveldb/table/table.cc:61:7
    #3 0x5591fc64ca56 in LLVMFuzzerTestOneInput /src/leveldb/build/../fuzz_table_open.cc:29:7
    #4 0x5591fc5011a0 in fuzzer::Fuzzer::ExecuteCallback(unsigned char const*, unsigned long) /src/llvm-project/compiler-rt/lib/fuzzer/FuzzerLoop.cpp:614:13
    #5 0x5591fc4ec415 in fuzzer::RunOneTest(fuzzer::Fuzzer*, char const*, unsigned long) /src/llvm-project/compiler-rt/lib/fuzzer/FuzzerDriver.cpp:327:6
    #6 0x5591fc4f1eaf in fuzzer::FuzzerDriver(int*, char***, int (*)(unsigned char const*, unsigned long)) /src/llvm-project/compiler-rt/lib/fuzzer/FuzzerDriver.cpp:862:9
    #7 0x5591fc51d152 in main /src/llvm-project/compiler-rt/lib/fuzzer/FuzzerMain.cpp:20:10
    #8 0x7eff7acd7082 in __libc_start_main (/lib/x86_64-linux-gnu/libc.so.6+0x24082) (BuildId: 5792732f783158c66fb4f3756458ca24e46e827d)

DEDUP_TOKEN: operator new[](unsigned long)--leveldb::ReadBlock(leveldb::RandomAccessFile*, leveldb::ReadOptions const&, leveldb::BlockHandle const&, leveldb::BlockContents*)--leveldb::Table::Open(leveldb::Options const&, leveldb::RandomAccessFile*, unsigned long, leveldb::Table**)
==14==HINT: if you don't care about these errors you may set allocator_may_return_null=1
SUMMARY: AddressSanitizer: out-of-memory /src/leveldb/table/format.cc:78:15 in leveldb::ReadBlock(leveldb::RandomAccessFile*, leveldb::ReadOptions const&, leveldb::BlockHandle const&, leveldb::BlockContents*)
==14==ABORTING

=== STDERR ===
INFO:__main__:Running: docker run --privileged --shm-size=2g --platform linux/amd64 --rm -i -e HELPER=True -e ARCHITECTURE=x86_64 -v /home/ubuntu/workspace/friday/tools/generate-harnesses/output/leveldb_a9fcfd3fbc7d492282c714b6e0b46723/build/out/leveldb:/out -v /home/ubuntu/workspace/friday/tools/generate-harnesses/output/leveldb_a9fcfd3fbc7d492282c714b6e0b46723/build/out/leveldb/crash-eb318a4efc67ba9452a00fc1e8bec0fd4bc8ecd3:/testcase -t gcr.io/oss-fuzz-base/base-runner reproduce fuzz_table_open -runs=100.

```

## Harness source
```c
#include <cstddef>
#include <cstdint>
#include <string>
#include <fstream>
#include <cstdio>
#include "leveldb/env.h"
#include "leveldb/table.h"
#include "leveldb/options.h"
#include "leveldb/status.h"
#include "leveldb/iterator.h"

// Table::Open reads SST files from disk (e.g. user-supplied .sst files).
extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
  const char* fname = "/tmp/fuzz_table_open.sst";
  std::ofstream out(fname, std::ios::binary);
  if (!out)
    return 0;
  out.write(reinterpret_cast<const char*>(data), size);
  out.close();

  leveldb::Options options;
  options.env = leveldb::Env::Default();
  leveldb::RandomAccessFile* file = nullptr;
  leveldb::Status s = options.env->NewRandomAccessFile(fname, &file);
  if (!s.ok())
    return 0;

  leveldb::Table* table = nullptr;
  s = leveldb::Table::Open(options, file, size, &table);
  if (!s.ok()) {
    delete file;
    return 0;
  }

  leveldb::Iterator* it = table->NewIterator(leveldb::ReadOptions());
  for (it->SeekToFirst(); it->Valid(); it->Next()) {}
  delete it;
  delete table;
  delete file;
  std::remove(fname);
  return 0;
}
```

## Crashing input (hexdump)
```text
00000000: ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff  ................
00000010: ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff  ................
00000020: ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff fe  ................
00000030: ff 57 fb 80 8b 24 75 47 db ff ff ff ff ff 3f 3d  .W...$uG......?=
00000040: 00 00 ff ff ff ff ff ff ff ff f7 ff ff ff ff ff  ................
00000050: 02 80 a8 0e 80 8b ff ff ff ff ff 57 fb 80 8b 24  ...........W...$
00000060: 75 47 db                                         uG.

```
