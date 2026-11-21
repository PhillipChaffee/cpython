/* Proof harness for v_lshift() in Objects/longobject.c.
 *
 * Callers: x_divrem() shifts w1 and v1 into fresh ints w and v (same digit
 * count) by d = PyLong_SHIFT - bit_length_digit(top digit of w1), so 0 <= d
 * < PyLong_SHIFT; _PyLong_Frexp() and long_true_divide() shift an int's
 * digits into a separate buffer by shift % PyLong_SHIFT.  v_lshift()
 * asserts 0 <= d < PyLong_SHIFT.  The source is the digit array of an int
 * (digits <= PyLong_MASK).  No caller passes z == a, but the loop reads
 * a[i] before it writes z[i], so the harness lets z and a alias too
 * (a superset of the callers' inputs).  z and a hold exactly m digits.
 *
 * Postcondition (T2): z[i] holds bits of a shifted left by d, computed per
 * digit from a0 (not by carrying), and the result is the d bits shifted out
 * of a[m-1]; every z[i] <= PyLong_MASK.
 */
#include "Objects/longobject.c"

#define MAXN 4

void harness(void)
{
    Py_ssize_t m;
    int d;
    __CPROVER_assume(0 <= m && m <= MAXN);              /* proof bound */
    __CPROVER_assume(0 <= d && d < PyLong_SHIFT);       /* v_lshift(): assert */
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

    digit carry = v_lshift(z, a, m, d);

    for (Py_ssize_t i = 0; i < m; i++) {
        uint64_t lo = ((uint64_t)a0[i] << d) & PyLong_MASK;
        uint64_t hi = i > 0 ? (uint64_t)a0[i - 1] >> (PyLong_SHIFT - d) : 0;
        __CPROVER_assert(z[i] == (digit)(lo | hi), "z[i] is digit i of a << d");
    }
    __CPROVER_assert(carry == (m > 0 ? (digit)((uint64_t)a0[m - 1] >> (PyLong_SHIFT - d)) : 0),
                     "the result is the bits shifted out of the top digit");
    if (!alias) {
        for (Py_ssize_t i = 0; i < m; i++) {
            __CPROVER_assert(a[i] == a0[i], "a is unchanged");
        }
        free(z);
    }
    free(a);
    free(a0);
}
