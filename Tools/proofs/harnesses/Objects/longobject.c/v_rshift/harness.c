/* Proof harness for v_rshift() in Objects/longobject.c.
 *
 * Callers: x_divrem() unshifts the remainder v0 into w0 (separate ints) by
 * the d it shifted with, 0 <= d < PyLong_SHIFT; _PyLong_Frexp() and
 * long_true_divide() shift an int's digits (from shift_digits on) into a
 * separate buffer by a remainder mod PyLong_SHIFT.  v_rshift() asserts 0 <=
 * d < PyLong_SHIFT.  The source is the digit array of an int (digits <=
 * PyLong_MASK).  No caller passes z == a, but the loop runs from the top and
 * reads a[i] before it writes z[i], so the harness lets them alias too.
 * z and a hold exactly m digits.
 *
 * Postcondition (T2): z[i] is digit i of a >> d, computed per digit from a0
 * (not by carrying), and the result is the d bits shifted out of a[0].
 */
#include "Objects/longobject.c"

#define MAXN 4

void harness(void)
{
    Py_ssize_t m;
    int d;
    __CPROVER_assume(0 <= m && m <= MAXN);              /* proof bound */
    __CPROVER_assume(0 <= d && d < PyLong_SHIFT);       /* v_rshift(): assert */
    digit *a = malloc(m * sizeof(digit));
    digit *a0 = malloc(m * sizeof(digit));
    /* The callers' arrays exist: the harness's own allocations may not fail. */
    __CPROVER_assume(a != NULL && a0 != NULL);
    int alias;
    digit *z = alias ? a : malloc(m * sizeof(digit));
    __CPROVER_assume(z != NULL);
    for (Py_ssize_t i = 0; i < m; i++) {
        __CPROVER_assume(a[i] <= PyLong_MASK);          /* PyLong digit invariant */
        a0[i] = a[i];
    }

    digit rem = v_rshift(z, a, m, d);

    for (Py_ssize_t i = 0; i < m; i++) {
        uint64_t lo = (uint64_t)a0[i] >> d;
        uint64_t hi = i + 1 < m ? ((uint64_t)a0[i + 1] << (PyLong_SHIFT - d)) & PyLong_MASK : 0;
        __CPROVER_assert(z[i] == (digit)(lo | hi), "z[i] is digit i of a >> d");
    }
    __CPROVER_assert(rem == (m > 0 ? a0[0] & (((digit)1 << d) - 1) : 0),
                     "the result is the bits shifted out of the bottom digit");
    if (!alias) {
        for (Py_ssize_t i = 0; i < m; i++) {
            __CPROVER_assert(a[i] == a0[i], "a is unchanged");
        }
        free(z);
    }
    free(a);
    free(a0);
}
