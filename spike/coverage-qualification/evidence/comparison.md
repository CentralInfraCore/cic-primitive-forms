### Rust: `cargo llvm-cov` with `-Zcoverage-options=condition`

| function | category | hand edges | hand covered full / reduced | tool outcomes | tool covered full / reduced |
|---|---|---|---|---|---|
| cond | conditional | 2 | 2 / 1 | 2 | 2 / 1 |
| let_opt | conditional | 2 | 2 / 2 | 2 | 2 / 2 |
| m | match | 5 | 5 / 3 | 2 | 2 / 1 |
| w | loop | 2 | 2 / 2 | 2 | 2 / 2 |
| wl | loop | 2 | 2 / 2 | 2 | 2 / 2 |
| fo | loop | 2 | 2 / 1 | 0 | 0 / 0 |
| lp | conditional | 2 | 2 / 2 | 2 | 2 / 2 |
| sc | short_circuit | 4 | 4 / 3 | 4 | 4 / 3 |
| so | short_circuit | 4 | 4 / 3 | 4 | 4 / 3 |
| q | error_path | 2 | 2 / 1 | 0 | 0 / 0 |

| category | hand edges | tool outcomes | hand edges the tool does not report |
|---|---|---|---|
| conditional | 6 | 6 | 0 |
| match | 5 | 2 | 3 |
| loop | 6 | 4 | 2 |
| short_circuit | 8 | 8 | 0 |
| error_path | 2 | 0 | 2 |
| **all** | 27 | 20 | 7 |

### Go: `gobco` (condition coverage)

| function | category | hand edges | hand covered full / reduced | tool outcomes | tool covered full / reduced |
|---|---|---|---|---|---|
| Cond | conditional | 2 | 2 / 1 | 2 | 2 / 1 |
| CommaOk | conditional | 2 | 2 / 2 | 2 | 2 / 2 |
| SwitchNoDefault | match | 3 | 3 / 2 | 4 | 4 / 3 |
| SwitchDefault | match | 2 | 2 / 1 | 2 | 2 / 1 |
| TypeSwitch | match | 3 | 3 / 2 | 4 | 4 / 3 |
| SelectDefault | match | 2 | 2 / 1 | 0 | 0 / 0 |
| SelectNoDefault | match | 2 | 2 / 1 | 0 | 0 / 0 |
| ForCond | loop | 2 | 2 / 2 | 2 | 2 / 2 |
| ForClause | loop | 2 | 2 / 1 | 2 | 2 / 1 |
| ForRange | loop | 2 | 2 / 2 | 0 | 0 / 0 |
| ForEver | conditional | 2 | 2 / 2 | 2 | 2 / 2 |
| And | short_circuit | 4 | 4 / 3 | 4 | 4 / 3 |
| Or | short_circuit | 4 | 4 / 3 | 4 | 4 / 3 |
| Atoi | conditional | 2 | 2 / 1 | 2 | 2 / 1 |

| category | hand edges | tool outcomes | hand edges the tool does not report |
|---|---|---|---|
| conditional | 8 | 8 | 0 |
| match | 12 | 10 | 2 |
| loop | 6 | 4 | 2 |
| short_circuit | 8 | 8 | 0 |
| error_path | 0 | 0 | 0 |
| **all** | 34 | 30 | 4 |

