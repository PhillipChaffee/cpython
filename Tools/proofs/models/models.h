/* Models of CPython's runtime for proof harnesses (see README.md, "Models").
 *
 * A harness that uses a model lists it in proof.toml `models`; this header is
 * the entry "models".  Include it after the source under proof.  Every model
 * is meant to be a sound over-approximation: it allows every behaviour of
 * the real function that a caller can observe, and each function documents
 * where it is narrower than that.
 */
#pragma once

#include "Python.h"
#include <stdbool.h>

/* alloc.c: PyMem_*, PyObject_* (Malloc, Calloc, Realloc, Free),
 * PyMem_Raw*, _PyObject_GC_New, _PyObject_GC_NewVar, PyObject_GC_Del.
 * Every allocating function can return NULL on any call. */

/* object.c: objects that already exist when the code under proof runs.
 * None of these returns NULL; each object is well-formed (see object.c). */
PyLongObject *model_nondet_long(Py_ssize_t max_digits);
PyObject *model_nondet_bytes(Py_ssize_t max_len);
PyObject *model_nondet_unicode(Py_ssize_t max_len);
PyObject *model_nondet_object(void);

/* errors.c: the error indicator.  model_current_exception is the type of
 * the pending exception, or NULL when no error is set. */
extern PyObject *model_current_exception;
bool model_error_is_set(void);

/* callee.c: stand-in for any call through a tp_* slot or function pointer.
 * Returns NULL with the error set, or a new reference to
 * model_nondet_object(). */
PyObject *model_generic_call(void);

/* runtime.c: _PyRuntime (with its real static initializer), the current
 * thread state and interpreter.  Call model_init_runtime() first in
 * harness() so the int free list holds what a running interpreter can hold. */
void model_init_runtime(void);
