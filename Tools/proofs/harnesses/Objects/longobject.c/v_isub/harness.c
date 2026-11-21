/* Proof harness for v_isub() in Objects/longobject.c.
 *
 * Callers: k_mul() subtracts t2 (= ah*bh) and t1 (= al*bl) from
 * ret->long_value.ob_digit + shift with m = i (digits left in ret after
 * shift) and n = _PyLong_DigitCount(t2) or _PyLong_DigitCount(t1).  Both
 * pass digit arrays of PyLong objects, whose digits are <= PyLong_MASK, and
 * rely on m >= n (v_isub() documents it; the comment above k_mul() proves
 * there is room).  The arrays hold exactly m and n digits, so any read past
 * them is a bounds failure.  Postcondition (T2): x holds the low m digits of
 * x0 - y0 (mod BASE**m), the returned borrow is the borrow out of the top
 * digit, and y is unchanged.
 *
 * The run also enforces v_isub's contract in contracts/longobject.h.
 */
#include "Objects/longobject.c"
#include "Tools/proofs/contracts/longobject.h"

#define MAXN LONGOBJECT_CONTRACT_MAX_DIGITS

void harness(void)
{
    Py_ssize_t m, n;
    __CPROVER_assume(0 <= n && n <= m);     /* v_isub(): "m >= n required" */
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

    digit borrow = v_isub(x, m, y, n);

    /* Independent reference: schoolbook subtraction in signed arithmetic. */
    int b = 0;
    for (Py_ssize_t i = 0; i < m; i++) {
        long long t = (long long)x0[i] - (i < n ? (long long)y0[i] : 0) - b;
        b = t < 0;
        if (b) {
            t += (long long)PyLong_BASE;
        }
        __CPROVER_assert(x[i] == (digit)t, "x[i] is digit i of x0 - y0");
    }
    __CPROVER_assert(borrow == (digit)b, "returned borrow is the borrow out of digit m-1");
    for (Py_ssize_t i = 0; i < n; i++) {
        __CPROVER_assert(y[i] == y0[i], "y is unchanged");
    }
    free(x0);
    free(y0);
    free(x);
    free(y);
}
