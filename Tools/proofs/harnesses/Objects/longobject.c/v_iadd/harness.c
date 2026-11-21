/* Proof harness for v_iadd() in Objects/longobject.c.
 *
 * Callers: k_mul() adds t3 into ret->long_value.ob_digit + shift with
 * m = i (digits left in ret after shift) and n = _PyLong_DigitCount(t3);
 * k_lopsided_mul() adds product into ret->long_value.ob_digit + nbdone with
 * m = _PyLong_DigitCount(ret) - nbdone and n = _PyLong_DigitCount(product).
 * Both pass digit arrays of PyLong objects, whose digits are <= PyLong_MASK,
 * and both rely on m >= n (v_iadd() asserts it; NDEBUG removes the assert).
 * The arrays hold exactly m and n digits, so any read past them is a bounds
 * failure.  Postcondition (T2): x holds the low m digits of x0 + y0, the
 * returned carry is the carry out of the top digit, and y is unchanged.
 *
 * The run also enforces v_iadd's contract in contracts/longobject.h
 * (enforce_contract): its requires are assumed, its ensures and assigns
 * checked, so callers' harnesses may replace v_iadd by it.
 */
#include "Objects/longobject.c"
#include "Tools/proofs/contracts/longobject.h"

#define MAXN LONGOBJECT_CONTRACT_MAX_DIGITS

void harness(void)
{
    Py_ssize_t m, n;
    __CPROVER_assume(0 <= n && n <= m);     /* v_iadd(): assert(m >= n) */
    __CPROVER_assume(m <= MAXN);            /* proof bound */
    digit *x = malloc(m * sizeof(digit));
    digit *y = malloc(n * sizeof(digit));
    /* The callers' arrays exist: the harness's own allocations may not fail. */
    __CPROVER_assume(x != NULL && y != NULL);
    for (Py_ssize_t i = 0; i < m; i++) {
        __CPROVER_assume(x[i] <= PyLong_MASK);  /* PyLong digit invariant */
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        __CPROVER_assume(y[i] <= PyLong_MASK);  /* PyLong digit invariant */
    }

    digit *x0 = malloc(m * sizeof(digit));
    digit *y0 = malloc(n * sizeof(digit));
    __CPROVER_assume(x0 != NULL && y0 != NULL);     /* the reference's copies */
    for (Py_ssize_t i = 0; i < m; i++) {
        x0[i] = x[i];
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        y0[i] = y[i];
    }

    digit carry = v_iadd(x, m, y, n);

    /* Independent reference: schoolbook addition of y0 (zero-extended to m
     * digits) into x0.  Digits are < 2**PyLong_SHIFT, so t < 2**(SHIFT+1)
     * fits in a digit. */
    digit c = 0;
    for (Py_ssize_t i = 0; i < m; i++) {
        digit t = x0[i] + (i < n ? y0[i] : 0) + c;
        __CPROVER_assert(x[i] == (t & PyLong_MASK), "x[i] is digit i of x0 + y0");
        c = t >> PyLong_SHIFT;
    }
    __CPROVER_assert(carry == c, "returned carry is the carry out of digit m-1");
    __CPROVER_assert(carry == 0 || carry == 1, "carry is 0 or 1");
    for (Py_ssize_t i = 0; i < n; i++) {
        __CPROVER_assert(y[i] == y0[i], "y is unchanged");
    }
    free(x0);
    free(y0);
    free(x);
    free(y);
}
