/* model_nondet_* objects are well-formed (test_model_objects_are_well_formed). */
#include "models.h"
#include "pycore_long.h"

void harness(void)
{
    PyLongObject *v = model_nondet_long(4);
    Py_ssize_t n = _PyLong_DigitCount(v);
    __CPROVER_assert(0 <= n && n <= 4, "long: digit count in [0, 4]");
    __CPROVER_assert(Py_IS_TYPE((PyObject *)v, &PyLong_Type), "long: type is int");
    __CPROVER_assert(Py_REFCNT(v) >= 1, "long: refcount >= 1");
    __CPROVER_assert((v->long_value.lv_tag & SIGN_MASK) != 3, "long: sign field valid");
    __CPROVER_assert((n == 0) == _PyLong_IsZero(v), "long: zero iff no digits");
    for (Py_ssize_t i = 0; i < n; i++) {
        __CPROVER_assert(v->long_value.ob_digit[i] <= PyLong_MASK, "long: digit <= PyLong_MASK");
    }
    if (n > 0) {
        __CPROVER_assert(v->long_value.ob_digit[n - 1] != 0, "long: normalised");
    }
    if (_PyLong_IsNegative(v)) {
        __CPROVER_assert(n > 0, "long: negative values occur");
    }
    if (n == 4) {
        __CPROVER_assert(1, "long: the bound is reached");
    }

    PyObject *b = model_nondet_bytes(4);
    __CPROVER_assert(PyBytes_CheckExact(b), "bytes: type is bytes");
    __CPROVER_assert(0 <= Py_SIZE(b) && Py_SIZE(b) <= 4, "bytes: size in [0, 4]");
    __CPROVER_assert(PyBytes_AS_STRING(b)[Py_SIZE(b)] == '\0', "bytes: NUL-terminated");
    __CPROVER_assert(Py_REFCNT(b) >= 1, "bytes: refcount >= 1");

    PyObject *u = model_nondet_unicode(4);
    __CPROVER_assert(PyUnicode_CheckExact(u), "str: type is str");
    __CPROVER_assert(PyUnicode_IS_COMPACT_ASCII(u), "str: compact ASCII");
    Py_ssize_t len = PyUnicode_GET_LENGTH(u);
    __CPROVER_assert(0 <= len && len <= 4, "str: length in [0, 4]");
    const unsigned char *data = PyUnicode_1BYTE_DATA(u);
    for (Py_ssize_t i = 0; i < len; i++) {
        __CPROVER_assert(data[i] < 128, "str: ASCII");
    }
    __CPROVER_assert(data[len] == 0, "str: NUL-terminated");

    PyObject *o = model_nondet_object();
    __CPROVER_assert(o != NULL && Py_REFCNT(o) >= 1, "object: refcount >= 1");
    if (PyLong_CheckExact(o)) {
        __CPROVER_assert(_PyLong_DigitCount((PyLongObject *)o) <= 2, "object: may be an int");
    }
    if (PyBytes_CheckExact(o)) {
        __CPROVER_assert(Py_SIZE(o) <= 4, "object: may be bytes");
    }
    if (PyUnicode_CheckExact(o)) {
        __CPROVER_assert(PyUnicode_GET_LENGTH(o) <= 4, "object: may be a str");
    }
}
