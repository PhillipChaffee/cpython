/* End-to-end use of the real v_iadd/v_isub contracts (contracts/
 * longobject.h), enforced by harnesses/Objects/longobject.c/v_iadd and
 * v_isub, replaced here in a caller.  No real caller is reachable at a
 * bound CBMC can handle: k_mul() calls them only for operands above
 * KARATSUBA_CUTOFF (70 digits), and k_lopsided_mul() asserts asize >
 * KARATSUBA_CUTOFF.  So the caller is add_then_sub() below, defined in
 * harness code; the proof shows the replaced calls' requires are checked
 * at the call site and their ensures assumed after it.
 */
#include "Objects/longobject.c"
#include "Tools/proofs/contracts/longobject.h"

static digit
add_then_sub(digit *x, Py_ssize_t m, digit *y, Py_ssize_t n)
{
    digit carry = v_iadd(x, m, y, n);
    digit borrow = v_isub(x, m, y, n);
    return carry + borrow;
}

void harness(void)
{
    Py_ssize_t m, n;
    __CPROVER_assume(0 <= n && n <= m && m <= LONGOBJECT_CONTRACT_MAX_DIGITS);
    digit *x = malloc(m * sizeof(digit));
    digit *y = malloc(n * sizeof(digit));
    __CPROVER_assume(x != NULL && y != NULL);
    for (Py_ssize_t i = 0; i < m; i++) {
        __CPROVER_assume(x[i] <= PyLong_MASK);
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        __CPROVER_assume(y[i] <= PyLong_MASK);
    }
    digit r = add_then_sub(x, m, y, n);
    __CPROVER_assert(r <= 2, "carry + borrow <= 2 (from the ensures)");
    for (Py_ssize_t i = 0; i < m; i++) {
        __CPROVER_assert(x[i] <= PyLong_MASK, "digits stay in range (from the ensures)");
    }
    free(x);
    free(y);
}
