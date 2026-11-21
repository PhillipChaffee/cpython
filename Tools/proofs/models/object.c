/* Object models: nondeterministic well-formed objects, and _Py_NewReference.
 *
 * model_nondet_*() stand for an object that already exists when the code
 * under proof runs (an argument, an attribute, a call's result).  They
 * never return NULL: the memory comes from __CPROVER_allocate, which does
 * not fail, because an existing object has no allocation left to fail.
 * Each object is a fresh CBMC object of exactly the size its contents
 * need, so any access past them is a bounds failure.  Real objects can have
 * spare capacity (long_normalize() does not shrink); reading it is a bug
 * in the code under proof, so the exact size is the stricter choice.
 *
 * Reference counts: any count in [1, 2**30], or immortal.
 * A local declared without an initialiser is a nondeterministic value.
 */
#include "Python.h"
#include "pycore_long.h"        // TAG_FROM_SIGN_AND_SIZE, SIGN_*
#include "pycore_object.h"
#include "models.h"

#define MODEL_OBJECT_MAX_DIGITS 2
#define MODEL_OBJECT_MAX_LEN 4

/* Contract: op's reference count becomes any value in [1, 2**30] or the
 * immortal initial count; its flags (ob_flags, ob_overflow) are zero. */
static void
model_set_refcnt(PyObject *op)
{
    int immortal;
    uint32_t count;
    op->ob_refcnt_full = immortal ? (int64_t)_Py_IMMORTAL_INITIAL_REFCNT
                                  : (int64_t)1 + (count & 0x3fffffff);
}

/* Contract: n in [0, max]. */
static Py_ssize_t
model_size(Py_ssize_t max)
{
    __CPROVER_assert(max >= 0, "model size bound is not negative");
    Py_ssize_t n;
    return (0 <= n && n <= max) ? n : 0;
}

/* Contract (Objects/object.c new_reference, default build, 64-bit): sets
 * the reference count to 1.  The reference tracer hook
 * (_PyReftracerTrack) and tracemalloc are not modelled: neither changes op. */
void
_Py_NewReference(PyObject *op)
{
    op->ob_refcnt_full = 1;
}

/* Contract: a PyLongObject of type PyLong_Type, laid out as
 * Include/cpython/longintrepr.h defines it: lv_tag = ndigits << 3 | sign,
 * with 0 <= ndigits <= max_digits, sign SIGN_ZERO iff ndigits == 0, else
 * positive or SIGN_NEGATIVE.  Every digit is <= PyLong_MASK and the top
 * digit is not zero (normalised).  Room for max(ndigits, 1) digits, as
 * long_alloc() allocates, and at least sizeof(PyLongObject) bytes, as the
 * PyObject_Malloc model gives (see alloc.c: CBMC needs the whole
 * long_value member), so a read of ob_digit[1] of a 0- or 1-digit int is
 * not reported.  A compact value in the small-int range may carry
 * the immortality bit with an immortal count, as _PyLong_SMALL_INTS do;
 * no value is the actual singleton. */
PyLongObject *
model_nondet_long(Py_ssize_t max_digits)
{
    Py_ssize_t ndigits = model_size(max_digits);
    Py_ssize_t room = ndigits ? ndigits : 1;
    size_t size = offsetof(PyLongObject, long_value.ob_digit) + room * sizeof(digit);
    PyLongObject *op = __CPROVER_allocate(
        size < sizeof(PyLongObject) ? sizeof(PyLongObject) : size, 0);
    op->ob_base.ob_type = &PyLong_Type;
    model_set_refcnt((PyObject *)op);
    for (Py_ssize_t i = 0; i < ndigits; i++) {
        digit d;
        op->long_value.ob_digit[i] = d & PyLong_MASK;
    }
    if (ndigits > 0 && op->long_value.ob_digit[ndigits - 1] == 0) {
        digit d;
        op->long_value.ob_digit[ndigits - 1] = 1 + d % PyLong_MASK;
    }
    int negative;
    int sign = ndigits == 0 ? 0 : negative ? -1 : 1;
    if (ndigits == 0) {
        op->long_value.ob_digit[0] = 0;
    }
    op->long_value.lv_tag = TAG_FROM_SIGN_AND_SIZE(sign, (size_t)ndigits);
    int small;
    if (small && ndigits <= 1) {
        stwodigits v = sign * (stwodigits)op->long_value.ob_digit[0];
        if (_PY_IS_SMALL_INT(v)) {
            op->long_value.lv_tag |= IMMORTALITY_BIT_MASK;
            op->ob_base.ob_refcnt_full = (int64_t)_Py_IMMORTAL_INITIAL_REFCNT;
        }
    }
    return op;
}

/* Contract: a PyBytesObject of type PyBytes_Type with 0 <= ob_size <=
 * max_len arbitrary bytes, ob_sval[ob_size] == '\0', room for exactly
 * ob_size + 1 bytes, and any ob_shash (the cache is not checked against
 * the contents). */
PyObject *
model_nondet_bytes(Py_ssize_t max_len)
{
    Py_ssize_t len = model_size(max_len);
    PyBytesObject *op = __CPROVER_allocate(offsetof(PyBytesObject, ob_sval) + len + 1, 0);
    op->ob_base.ob_base.ob_type = &PyBytes_Type;
    model_set_refcnt((PyObject *)op);
    op->ob_base.ob_size = len;
    op->ob_sval[len] = '\0';
    return (PyObject *)op;
}

/* Contract: a compact ASCII str (PyASCIIObject followed by its data) of
 * type PyUnicode_Type: 0 <= length <= max_len characters, each < 128,
 * then a NUL; kind 1, compact 1, ascii 1, not interned, not statically
 * allocated; hash -1 or any value.  NOT modelled: compact non-ASCII
 * strings (kinds 1, 2, 4 with ascii 0) and legacy (non-compact) strings,
 * so code that handles only those is not reached through this model. */
PyObject *
model_nondet_unicode(Py_ssize_t max_len)
{
    Py_ssize_t len = model_size(max_len);
    PyASCIIObject *op = __CPROVER_allocate(sizeof(PyASCIIObject) + len + 1, 0);
    op->ob_base.ob_type = &PyUnicode_Type;
    model_set_refcnt((PyObject *)op);
    op->length = len;
    Py_hash_t hash;
    op->hash = hash;
    op->state.interned = 0;
    op->state.kind = PyUnicode_1BYTE_KIND;
    op->state.compact = 1;
    op->state.ascii = 1;
    op->state.statically_allocated = 0;
    unsigned char *data = (unsigned char *)(op + 1);
    for (Py_ssize_t i = 0; i < len; i++) {
        unsigned char c;
        data[i] = c & 0x7f;
    }
    data[len] = 0;
    return (PyObject *)op;
}

/* A type with no slots: code that calls a slot of a model_nondet_object()
 * result whose type is this one dereferences NULL, a reported failure, not
 * a silent assumption. */
static PyTypeObject model_opaque_type = {
    .ob_base = { .ob_base = { .ob_refcnt_full = _Py_IMMORTAL_INITIAL_REFCNT } },
    .tp_name = "model-opaque",
    .tp_basicsize = sizeof(PyObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
};

/* Contract: a model_nondet_long (up to 2 digits), model_nondet_bytes or
 * model_nondet_unicode (up to 4), or an object of a type with no slots
 * and no instance data.  Not modelled: objects of any other type. */
PyObject *
model_nondet_object(void)
{
    int which;
    switch (which) {
    case 0:
        return (PyObject *)model_nondet_long(MODEL_OBJECT_MAX_DIGITS);
    case 1:
        return model_nondet_bytes(MODEL_OBJECT_MAX_LEN);
    case 2:
        return model_nondet_unicode(MODEL_OBJECT_MAX_LEN);
    default: {
        PyObject *op = __CPROVER_allocate(sizeof(PyObject), 0);
        op->ob_type = &model_opaque_type;
        model_set_refcnt(op);
        return op;
    }
    }
}
