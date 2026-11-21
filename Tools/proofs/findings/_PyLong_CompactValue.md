# _PyLong_CompactValue: out-of-range unsigned-to-signed conversion

**Severity:** implementation-defined behaviour, not undefined behaviour.
No miscompilation is expected on gcc or clang. Recorded so that the
`--conversion-check` waiver in `harnesses/Objects/longobject.c/x_sub` has a
reference.

**Where:** `Include/cpython/longintrepr.h`, `_PyLong_CompactValue()`:

```c
Py_ssize_t sign;
...
sign = 1 - (op->long_value.lv_tag & _PyLong_SIGN_MASK);
```

`lv_tag` is `uintptr_t`, so `1 - (lv_tag & 3)` is computed in `uintptr_t`.
For a negative int (sign bits `2`), the value is `UINTPTR_MAX`, which is out
of range for `Py_ssize_t`. C11 6.3.1.3p3 (and C23) make the result of that
conversion implementation-defined. gcc and clang define it as reduction
modulo 2**64, which gives the intended `-1`.

**Found by:** the x_sub harness (T1 checks include `--conversion-check`):

```
fail 264.4s Objects/longobject.c/x_sub
  _PyLong_CompactValue.overflow.1: arithmetic overflow on unsigned to signed
  type conversion in (Py_ssize_t)((unsigned long int)1 -
  (op->long_value.lv_tag & (unsigned long int)3))
```

Trace: x_sub(a, b) with |a| < |b|, a 1-digit result, so `_PyLong_FlipSign(z)`
sets the sign bits to 2; then `maybe_small_long(z)` calls
`medium_value(z)` -> `_PyLong_CompactValue(z)` on the negative compact int.
Every negative compact int reaches it (`-1 - 1`, for example).

**Possible fix (not applied; CPython sources are not edited here):** compute
the sign in a signed type, for example
`sign = 1 - (Py_ssize_t)(op->long_value.lv_tag & _PyLong_SIGN_MASK);`
(`_PyLong_SignedDigitCount()`, `_PyLong_CompactSign()` and
`_PyLong_NonCompactSign()` in `Include/internal/pycore_long.h` have the same
pattern).

**Status:** not triaged upstream. The x_sub harness disables
`--conversion-check` with this file as its reason; the other checks stay on.
