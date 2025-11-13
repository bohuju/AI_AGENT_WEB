# Crash Analysis for fuzz_table_open Crash

## 1. Bug Type
- Denial-of-Service (DoS) via unbounded memory allocation

## 2. Bug Summary
Feeding arbitrary data as an SSTable file to `leveldb::Table::Open` can trigger an out-of-memory crash. The fuzzer input coincidentally contains the valid LevelDB table magic value, causing the parser to proceed. A malformed block handle is decoded with an extremely large `size` field, leading to a huge allocation request in `ReadBlock` and an AddressSanitizer OOM abort.

## 3. Bug Impact (real world reachability/exploitability/constraints)
- An attacker controlling SSTable input can cause the library to abort or consume excessive memory (denial-of-service).
- Requires supplying a crafted `.sst` file; not exploitable via normal database operations unless untrusted SST files are loaded.
- **severity:** Medium

## 4. How to Patch
- Validate decoded block handle fields before allocating memory:
  - Ensure `offset + size` does not overflow and stays within the file bounds (`file_size`).
  - Impose a reasonable maximum block size threshold or fail gracefully on suspicious values.
- Return an error status from `Table::Open`/`ReadBlock` instead of proceeding to allocate if validation fails.