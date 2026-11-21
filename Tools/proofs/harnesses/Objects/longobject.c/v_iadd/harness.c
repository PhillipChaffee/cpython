/* Proof harness for v_iadd() in Objects/longobject.c.
 *
 * Callers: k_mul() adds t3 into ret->long_value.ob_digit + shift with
 * m = i (digits left in ret after shift) and n = _PyLong_DigitCount(t3);
 * k_lopsided_mul() adds product into ret->long_value.ob_digit + nbdone with
 * m = _PyLong_DigitCount(ret) - nbdone and n = _PyLong_DigitCount(product).
 * Both pass digit arrays of PyLong objects, whose digits are <= PyLong_MASK,
 * and both rely on m >= n (v_iadd() asserts it; NDEBUG removes the assert).
 * The arrays hold exactly m and n digits, so any read past them is a bounds
 * failure.
 */
#include "Objects/longobject.c"

#define MAXN 8

void harness(void)
{
    Py_ssize_t m, n;
    __CPROVER_assume(0 <= n && n <= m);     /* v_iadd(): assert(m >= n) */
    __CPROVER_assume(m <= MAXN);            /* proof bound */
    digit *x = malloc(m * sizeof(digit));
    digit *y = malloc(n * sizeof(digit));
    for (Py_ssize_t i = 0; i < m; i++) {
        __CPROVER_assume(x[i] <= PyLong_MASK);  /* PyLong digit invariant */
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        __CPROVER_assume(y[i] <= PyLong_MASK);  /* PyLong digit invariant */
    }

    digit carry = v_iadd(x, m, y, n);

    __CPROVER_assert(carry == 0 || carry == 1, "carry is 0 or 1");
    for (Py_ssize_t i = 0; i < m; i++) {
        __CPROVER_assert(x[i] <= PyLong_MASK, "every output digit <= PyLong_MASK");
    }
    free(x);
    free(y);
}
