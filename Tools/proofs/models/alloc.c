/* Allocation models: PyMem_*, PyMem_Raw*, PyObject_* and the GC allocators.
 *
 * The real allocators (Objects/obmalloc.c, Python/gc.c) can fail on any call,
 * so every allocating model returns NULL nondeterministically, whatever
 * CBMC's own malloc does.  A successful allocation is a fresh CBMC object of
 * exactly the requested size, so any access past it is a bounds failure.
 * The real allocators may hand out more usable bytes than requested; using
 * them is undefined at the API level, so the exact size is the stricter
 * choice, with one exception: PyObject_Malloc/Calloc/Realloc give at least
 * MODEL_MIN_OBJECT bytes (see there).  Statistics, tracemalloc hooks, debug fill patterns and the
 * PYTHONMALLOC debug hooks are not modelled: none of them changes what the
 * caller can observe through the returned pointer.
 *
 * A local declared without an initialiser is a nondeterministic value.
 */
#include "Python.h"
#include "pycore_object.h"      // _PyType_PreHeaderSize, _PyObject_Init
#include "models.h"

#include <stdlib.h>

/* Contract: returns NULL, or a fresh object of exactly n bytes with
 * unspecified contents.  n > PY_SSIZE_T_MAX always gives NULL. */
static void *
model_malloc(size_t n)
{
    int fail;
    if (fail || n > (size_t)PY_SSIZE_T_MAX) {
        return NULL;
    }
    return malloc(n);
}

/* Contract: NULL, or nelem * elsize zero bytes; NULL on overflow. */
static void *
model_calloc(size_t nelem, size_t elsize)
{
    int fail;
    if (fail || (elsize != 0 && nelem > (size_t)PY_SSIZE_T_MAX / elsize)) {
        return NULL;
    }
    return calloc(nelem, elsize);
}

/* Contract: on NULL, p is untouched and still owned by the caller; else p
 * (if not NULL) is freed and the result holds min(old, n) of its bytes. */
static void *
model_realloc(void *p, size_t n)
{
    int fail;
    if (fail || n > (size_t)PY_SSIZE_T_MAX) {
        return NULL;
    }
    return realloc(p, n);
}

void *PyMem_RawMalloc(size_t n) { return model_malloc(n); }
void *PyMem_RawCalloc(size_t nelem, size_t elsize) { return model_calloc(nelem, elsize); }
void *PyMem_RawRealloc(void *p, size_t n) { return model_realloc(p, n); }
/* Contract: p is NULL or a live block from these allocators; it is freed. */
void PyMem_RawFree(void *p) { free(p); }

void *PyMem_Malloc(size_t n) { return model_malloc(n); }
void *PyMem_Calloc(size_t nelem, size_t elsize) { return model_calloc(nelem, elsize); }
void *PyMem_Realloc(void *p, size_t n) { return model_realloc(p, n); }
void PyMem_Free(void *p) { free(p); }

/* PyObject_* requests below MODEL_MIN_OBJECT bytes get MODEL_MIN_OBJECT
 * bytes, so a read in [n, 32) of such a block is not reported.  CBMC checks
 * a struct member access against the whole member, trailing padding
 * included: `z->long_value.ob_digit[0]` needs offsetof(PyLongObject,
 * long_value) + sizeof(_PyLongValue) = sizeof(PyLongObject) = 32 bytes,
 * while long_alloc() requests 28 for a 0- or 1-digit int.  The real
 * allocator always provides them: pymalloc (WITH_PYMALLOC, the default)
 * serves 17..32-byte requests from its 32-byte size class, and glibc's
 * smallest chunk has 24 usable bytes, 40 for a 28-byte request. */
#define MODEL_MIN_OBJECT 32
static size_t model_object_size(size_t n) { return n < MODEL_MIN_OBJECT ? MODEL_MIN_OBJECT : n; }

void *PyObject_Malloc(size_t n) { return model_malloc(model_object_size(n)); }
void *
PyObject_Calloc(size_t nelem, size_t elsize)
{
    void *p = model_calloc(nelem, elsize);
    if (p != NULL && nelem * elsize < MODEL_MIN_OBJECT) {
        free(p);
        p = model_calloc(1, MODEL_MIN_OBJECT);
    }
    return p;
}
void *PyObject_Realloc(void *p, size_t n) { return model_realloc(p, model_object_size(n)); }
void PyObject_Free(void *p) { free(p); }

/* Contract (Python/gc.c gc_alloc + _PyObject_Init): NULL with MemoryError
 * set, or an object of tp->tp_basicsize + extra bytes after a zeroed
 * pre-header of _PyType_PreHeaderSize(tp) bytes, with ob_type = tp and
 * refcount 1, not yet tracked by the GC.  The GC's allocation count and the
 * collection it may schedule are not modelled (the collection runs later,
 * at an eval breaker, not inside this call). */
static PyObject *
model_gc_alloc(PyTypeObject *tp, size_t basicsize)
{
    size_t presize = _PyType_PreHeaderSize(tp);
    if (basicsize > (size_t)PY_SSIZE_T_MAX - presize) {
        return PyErr_NoMemory();
    }
    char *mem = model_malloc(presize + basicsize);
    if (mem == NULL) {
        return PyErr_NoMemory();
    }
    memset(mem, 0, presize);
    PyObject *op = (PyObject *)(mem + presize);
    _PyObject_Init(op, tp);
    return op;
}

PyObject *
_PyObject_GC_New(PyTypeObject *tp)
{
    return model_gc_alloc(tp, _PyObject_SIZE(tp));
}

/* Contract: as _PyObject_GC_New, sized for nitems items, with ob_size =
 * nitems.  nitems < 0 is a SystemError (PyErr_BadInternalCall) in the real
 * function; here it is a property violation. */
PyVarObject *
_PyObject_GC_NewVar(PyTypeObject *tp, Py_ssize_t nitems)
{
    __CPROVER_assert(nitems >= 0, "_PyObject_GC_NewVar: nitems >= 0");
    if (tp->tp_itemsize != 0
        && (size_t)nitems > ((size_t)PY_SSIZE_T_MAX - _PyObject_SIZE(tp)) / (size_t)tp->tp_itemsize) {
        return (PyVarObject *)PyErr_NoMemory();
    }
    PyVarObject *op = (PyVarObject *)model_gc_alloc(tp, _PyObject_VAR_SIZE(tp, nitems));
    if (op != NULL) {
        op->ob_size = nitems;
    }
    return op;
}

/* Contract: op came from _PyObject_GC_New/_NewVar and is not tracked. */
void
PyObject_GC_Del(void *op)
{
    size_t presize = _PyType_PreHeaderSize(Py_TYPE((PyObject *)op));
    free((char *)op - presize);
}
