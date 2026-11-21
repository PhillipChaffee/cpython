/* Proof harness for x_sub() in Objects/longobject.c.
 *
 * Callers: long_add() and long_sub() call x_sub(a, b) or x_sub(b, a) with
 * two ints that are not both compact; k_mul() does not call it.  x_sub()
 * ignores the signs of its operands and has no precondition of its own, so
 * a and b are any well-formed ints (model_nondet_long(): normalised, digits
 * <= PyLong_MASK, either sign) of up to MAXD digits.
 *
 * Postcondition (T2): on NULL the error indicator is set; otherwise the
 * result is a new reference to a well-formed int (normalised, digits <=
 * PyLong_MASK, sign consistent with the digit count) whose value is
 * |a| - |b|, checked against the difference of the digit arrays in
 * __int128; a and b are unchanged.  Small results come from the real
 * small-int table (models/runtime.c), so they are checked too.
 *
 * A small-int result points into _PyRuntime (251 KB): reading it through
 * the result pointer makes CBMC extract bytes at a symbolic offset of that
 * whole object, which runs out of memory.  So the harness reads a
 * small-int result as an element of _PyLong_SMALL_INTS by index.
 */
#include "Objects/longobject.c"
#include "models.h"

#define MAXD 3

/* |v| from its digits (MAXD * PyLong_SHIFT = 90 bits fit). */
static __int128
magnitude(PyLongObject *v)
{
    __int128 r = 0;
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
    __int128 va = magnitude(a), vb = magnitude(b);
    uintptr_t tag_a = a->long_value.lv_tag, tag_b = b->long_value.lv_tag;

    PyLongObject *z = x_sub(a, b);

    __CPROVER_assert(a->long_value.lv_tag == tag_a && magnitude(a) == va, "a is unchanged");
    __CPROVER_assert(b->long_value.lv_tag == tag_b && magnitude(b) == vb, "b is unchanged");
    if (z == NULL) {
        __CPROVER_assert(model_error_is_set(), "NULL only with the error set");
    }
    else if (__CPROVER_same_object(z, _PyLong_SMALL_INTS)) {
        /* The condition is on z itself, so CBMC drops _PyRuntime from z's
         * possible targets in the other branch. */
        size_t off = (char *)z - (char *)_PyLong_SMALL_INTS;
        Py_ssize_t k = (Py_ssize_t)(off / sizeof(PyLongObject));
        __CPROVER_assert(off % sizeof(PyLongObject) == 0
                         && k < _PY_NSMALLNEGINTS + _PY_NSMALLPOSINTS,
                         "a result in _PyRuntime is an element of _PyLong_SMALL_INTS");
        __CPROVER_assert(!model_error_is_set(), "no error set on success");
        __CPROVER_assert(_PyLong_IsSmallInt(&_PyLong_SMALL_INTS[k]),
                         "the result is an immortal small int");
        __CPROVER_assert(k - _PY_NSMALLNEGINTS == va - vb, "the result is |a| - |b|");
    }
    else {
        Py_ssize_t n = _PyLong_DigitCount(z);
        __CPROVER_assert(!model_error_is_set(), "no error set on success");
        __CPROVER_assert(Py_IS_TYPE((PyObject *)z, &PyLong_Type) && Py_REFCNT(z) >= 1,
                         "the result is a live int");
        __CPROVER_assert(0 <= n && n <= MAXD, "the result has at most MAXD digits");
        __CPROVER_assert((z->long_value.lv_tag & SIGN_MASK) != 3, "the sign field is valid");
        __CPROVER_assert((n == 0) == _PyLong_IsZero(z), "zero iff no digits");
        for (Py_ssize_t i = 0; i < n; i++) {
            __CPROVER_assert(z->long_value.ob_digit[i] <= PyLong_MASK,
                             "every digit is <= PyLong_MASK");
        }
        if (n > 0) {
            __CPROVER_assert(z->long_value.ob_digit[n - 1] != 0, "the result is normalised");
        }
        __int128 vz = _PyLong_IsNegative(z) ? -magnitude(z) : magnitude(z);
        __CPROVER_assert(vz == va - vb, "the result is |a| - |b|");
    }
}
