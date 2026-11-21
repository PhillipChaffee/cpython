/* CBMC contracts for static functions of Objects/longobject.c.
 *
 * Include this header after the source (#include "Objects/longobject.c"),
 * by its path from the repository root:
 *     #include "Tools/proofs/contracts/longobject.h"
 * ("longobject.h" alone finds Include/longobject.h).  Each declaration
 * repeats the function's signature and attaches the contract; CPython's
 * sources are not edited.  A harness with `enforce_contract = "<f>"` checks
 * the contract against f's body; a harness with `replace_contracts =
 * ["<f>"]` replaces each call to f by it (see README.md, "Contracts").
 *
 * The contracts are bounded: they require at most
 * LONGOBJECT_CONTRACT_MAX_DIGITS digits, the bound the enforcing harnesses
 * prove, so a caller cannot use them beyond what was proved.  Quantifiers
 * have that constant bound too: CBMC 5.95.1 silently drops an assumed
 * __CPROVER_forall whose range is symbolic (see README.md).
 */
#pragma once

#define LONGOBJECT_CONTRACT_MAX_DIGITS 3

/* v_iadd: x[0:m] += y[0:n], m >= n; returns the carry out of x[m-1].
 * Ensures: the carry is 0 or 1 and every digit of x stays <= PyLong_MASK;
 * only x[0:m] is written (y is unchanged).  The value x0 + y0 = x + carry
 * * BASE**m is the T2 postcondition of the v_iadd harness, not part of
 * this contract. */
static digit
v_iadd(digit *x, Py_ssize_t m, digit *y, Py_ssize_t n)
__CPROVER_requires(0 <= n && n <= m && m <= LONGOBJECT_CONTRACT_MAX_DIGITS)
__CPROVER_requires(__CPROVER_rw_ok(x, m * sizeof(digit)))
__CPROVER_requires(__CPROVER_r_ok(y, n * sizeof(digit)))
__CPROVER_requires(__CPROVER_forall {
    Py_ssize_t i; (0 <= i && i < LONGOBJECT_CONTRACT_MAX_DIGITS && i < m) ==> x[i] <= PyLong_MASK })
__CPROVER_requires(__CPROVER_forall {
    Py_ssize_t i; (0 <= i && i < LONGOBJECT_CONTRACT_MAX_DIGITS && i < n) ==> y[i] <= PyLong_MASK })
__CPROVER_assigns(__CPROVER_object_upto(x, m * sizeof(digit)))
__CPROVER_ensures(__CPROVER_return_value <= 1)
__CPROVER_ensures(__CPROVER_forall {
    Py_ssize_t i; (0 <= i && i < LONGOBJECT_CONTRACT_MAX_DIGITS && i < m) ==> x[i] <= PyLong_MASK })
;

/* v_isub: x[0:m] -= y[0:n], m >= n; returns the borrow out of x[m-1].
 * Ensures: the borrow is 0 or 1 and every digit of x stays <=
 * PyLong_MASK; only x[0:m] is written (y is unchanged). */
static digit
v_isub(digit *x, Py_ssize_t m, digit *y, Py_ssize_t n)
__CPROVER_requires(0 <= n && n <= m && m <= LONGOBJECT_CONTRACT_MAX_DIGITS)
__CPROVER_requires(__CPROVER_rw_ok(x, m * sizeof(digit)))
__CPROVER_requires(__CPROVER_r_ok(y, n * sizeof(digit)))
__CPROVER_requires(__CPROVER_forall {
    Py_ssize_t i; (0 <= i && i < LONGOBJECT_CONTRACT_MAX_DIGITS && i < m) ==> x[i] <= PyLong_MASK })
__CPROVER_requires(__CPROVER_forall {
    Py_ssize_t i; (0 <= i && i < LONGOBJECT_CONTRACT_MAX_DIGITS && i < n) ==> y[i] <= PyLong_MASK })
__CPROVER_assigns(__CPROVER_object_upto(x, m * sizeof(digit)))
__CPROVER_ensures(__CPROVER_return_value <= 1)
__CPROVER_ensures(__CPROVER_forall {
    Py_ssize_t i; (0 <= i && i < LONGOBJECT_CONTRACT_MAX_DIGITS && i < m) ==> x[i] <= PyLong_MASK })
;
