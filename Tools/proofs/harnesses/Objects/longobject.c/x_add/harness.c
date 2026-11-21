/* Proof harness for x_add() in Objects/longobject.c.
 *
 * Callers: long_add() and long_sub() call x_add(a, b) with two ints that are
 * not both compact (_PyLong_BothAreCompact() is false), k_mul() with the
 * halves from kmul_split() (any sizes).  x_add() itself asserts that the
 * larger operand has at least one digit, so the weakest caller guarantee is
 * that a and b are well-formed ints, not both zero.  model_nondet_long()
 * gives any well-formed int (normalised, digits <= PyLong_MASK, either
 * sign: x_add() ignores signs) of up to MAXD digits.
 *
 * Postcondition (T2): on NULL the error indicator is set (MemoryError from
 * long_alloc()); otherwise the result is a new reference to a well-formed,
 * non-negative int whose value is |a| + |b|, checked against a sum of the
 * digit arrays in unsigned __int128, and a and b are unchanged.
 */
#include "Objects/longobject.c"
#include "models.h"

#define MAXD 3

/* |v| from its digits (MAXD * PyLong_SHIFT = 90 bits fit). */
static unsigned __int128
magnitude(PyLongObject *v)
{
    unsigned __int128 r = 0;
    for (Py_ssize_t i = _PyLong_DigitCount(v); i-- > 0;) {
        r = (r << PyLong_SHIFT) | v->long_value.ob_digit[i];
    }
    return r;
}

void harness(void)
{
    model_init_runtime();
    PyLongObject *a = model_nondet_long(MAXD);
    PyLongObject *b = model_nondet_long(MAXD);
    /* x_add(): assert(size_a >= 1) after ordering by size */
    __CPROVER_assume(_PyLong_DigitCount(a) >= 1 || _PyLong_DigitCount(b) >= 1);
    unsigned __int128 va = magnitude(a), vb = magnitude(b);
    uintptr_t tag_a = a->long_value.lv_tag, tag_b = b->long_value.lv_tag;

    PyLongObject *z = x_add(a, b);

    __CPROVER_assert(a->long_value.lv_tag == tag_a && magnitude(a) == va, "a is unchanged");
    __CPROVER_assert(b->long_value.lv_tag == tag_b && magnitude(b) == vb, "b is unchanged");
    if (z == NULL) {
        __CPROVER_assert(model_error_is_set(), "NULL only with the error set");
    }
    else {
        Py_ssize_t n = _PyLong_DigitCount(z);
        __CPROVER_assert(!model_error_is_set(), "no error set on success");
        __CPROVER_assert(Py_IS_TYPE((PyObject *)z, &PyLong_Type) && Py_REFCNT(z) >= 1,
                         "the result is a live int");
        __CPROVER_assert(0 < n && n <= MAXD + 1, "the result has 1 to MAXD + 1 digits");
        __CPROVER_assert(!_PyLong_IsNegative(z) && !_PyLong_IsZero(z),
                         "the result is positive");
        for (Py_ssize_t i = 0; i < n; i++) {
            __CPROVER_assert(z->long_value.ob_digit[i] <= PyLong_MASK,
                             "every digit is <= PyLong_MASK");
        }
        __CPROVER_assert(z->long_value.ob_digit[n - 1] != 0, "the result is normalised");
        __CPROVER_assert(magnitude(z) == va + vb, "the result is |a| + |b|");
    }
}
