/* Error indicator models (the real functions are in Python/errors.c).
 *
 * The ghost model_current_exception stands for the thread state's current
 * exception: it holds the exception's type, or NULL when no error is set.
 * It starts NULL: CPython calls a function with no error pending (the
 * functions that may be called with one set, such as PyErr_Fetch users,
 * need their own harness setup).  The exception's value and traceback are
 * not modelled; no pilot caller reads them.
 *
 * The exception types the models raise are defined here as distinct
 * immortal objects, so a harness can compare against them.
 */
#include "Python.h"
#include "pycore_pyerrors.h"    // _PyErr_SetString
#include "models.h"

PyObject *model_current_exception = NULL;

#define MODEL_EXC(NAME) \
    static PyObject model_exc_##NAME = { \
        .ob_refcnt_full = _Py_IMMORTAL_INITIAL_REFCNT, \
        .ob_type = NULL }; \
    PyObject *PyExc_##NAME = &model_exc_##NAME;

MODEL_EXC(MemoryError)
MODEL_EXC(OverflowError)
MODEL_EXC(ValueError)
MODEL_EXC(TypeError)
MODEL_EXC(SystemError)
MODEL_EXC(IndexError)
MODEL_EXC(ZeroDivisionError)
MODEL_EXC(RuntimeError)

/* Contract: true iff an error is set. */
bool
model_error_is_set(void)
{
    return model_current_exception != NULL;
}

/* Contract: exc is not NULL (every caller passes an exception type); the
 * error indicator is then set to exc, replacing any pending error. */
void
PyErr_SetString(PyObject *exc, const char *msg)
{
    (void)msg;
    __CPROVER_assert(exc != NULL, "PyErr_SetString: exception type is not NULL");
    model_current_exception = exc;
}

/* Contract: as PyErr_SetString. */
void
_PyErr_SetString(PyThreadState *tstate, PyObject *exc, const char *msg)
{
    (void)tstate;
    PyErr_SetString(exc, msg);
}

/* Contract: as PyErr_SetString; the value is not modelled. */
void
PyErr_SetObject(PyObject *exc, PyObject *value)
{
    (void)value;
    PyErr_SetString(exc, NULL);
}

/* Contract: as PyErr_SetString, returns NULL; the message is not modelled. */
PyObject *
PyErr_Format(PyObject *exc, const char *format, ...)
{
    (void)format;
    PyErr_SetString(exc, NULL);
    return NULL;
}

/* Contract: sets MemoryError, returns NULL. */
PyObject *
PyErr_NoMemory(void)
{
    model_current_exception = PyExc_MemoryError;
    return NULL;
}

/* Contract: sets SystemError (the real function's "bad argument to internal
 * function"). */
void
_PyErr_BadInternalCall(const char *filename, int lineno)
{
    (void)filename;
    (void)lineno;
    model_current_exception = PyExc_SystemError;
}

/* Contract: the type of the pending exception (borrowed), or NULL. */
PyObject *
PyErr_Occurred(void)
{
    return model_current_exception;
}

/* Contract: clears the error indicator. */
void
PyErr_Clear(void)
{
    model_current_exception = NULL;
}
