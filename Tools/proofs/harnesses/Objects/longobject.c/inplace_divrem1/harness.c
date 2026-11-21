/* Proof harness for inplace_divrem1() in Objects/longobject.c.
 *
 * Callers: divrem1() divides a fresh int's digits (size = digit count of a)
 * by n with 0 < n <= PyLong_MASK (it asserts this); long_true_divide()
 * divides x in place (pout == pin) by b's single digit, which is non-zero
 * because division by zero is rejected earlier; _PyLong_Format-style
 * callers use pin == pout too (the comment above the function).  So pin and
 * pout either alias or are separate arrays of exactly size digits, each
 * digit <= PyLong_MASK.
 *
 * Postcondition (T2): with q the digits written to pout and r the result,
 * pin0 = q * n + r with 0 <= r < n and every q digit <= PyLong_MASK (so q
 * and r are the quotient and remainder), checked in unsigned __int128.
 */
#include "Objects/longobject.c"

#define MAXN 2

static unsigned __int128
value(const digit *v, Py_ssize_t size)
{
    unsigned __int128 r = 0;
    for (Py_ssize_t i = size; i-- > 0;) {
        r = (r << PyLong_SHIFT) | v[i];
    }
    return r;
}

void harness(void)
{
    Py_ssize_t size;
    digit n;
    __CPROVER_assume(0 <= size && size <= MAXN);        /* proof bound */
    __CPROVER_assume(0 < n && n <= PyLong_MASK);        /* inplace_divrem1(): assert */
    digit *pin = malloc(size * sizeof(digit));
    digit *pin0 = malloc(size * sizeof(digit));
    /* The callers' arrays exist: the harness's own allocations may not fail. */
    __CPROVER_assume(pin != NULL && pin0 != NULL);
    int alias;
    digit *pout = alias ? pin : malloc(size * sizeof(digit));
    __CPROVER_assume(pout != NULL);
    for (Py_ssize_t i = 0; i < size; i++) {
        __CPROVER_assume(pin[i] <= PyLong_MASK);        /* PyLong digit invariant */
        pin0[i] = pin[i];
    }

    digit rem = inplace_divrem1(pout, pin, size, n);

    for (Py_ssize_t i = 0; i < size; i++) {
        __CPROVER_assert(pout[i] <= PyLong_MASK, "every quotient digit is <= PyLong_MASK");
    }
    __CPROVER_assert(rem < n, "the remainder is below n");
    /* q < BASE**MAXN = 2**60 and n < 2**30, so q * n + r < 2**90. */
    __CPROVER_assert(value(pout, size) * n + rem == value(pin0, size),
                     "pin = quotient * n + remainder");
    if (!alias) {
        for (Py_ssize_t i = 0; i < size; i++) {
            __CPROVER_assert(pin[i] == pin0[i], "pin is unchanged");
        }
        free(pout);
    }
    free(pin);
    free(pin0);
}
