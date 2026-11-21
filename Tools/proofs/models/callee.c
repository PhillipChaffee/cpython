/* The stand-in for a call the proof cannot follow: a tp_* slot, a function
 * pointer, a call back into Python.
 *
 * Contract: either sets the error indicator and returns NULL, or returns a
 * new reference to a model_nondet_object() with the error indicator left
 * as it was.  The real callee could also run arbitrary Python code that
 * changes global state; harnesses that use this model must not rely on any
 * global state the callee could change (say so in the harness).
 */
#include "Python.h"
#include "models.h"

PyObject *
model_generic_call(void)
{
    int fails;
    if (fails) {
        PyErr_SetString(PyExc_RuntimeError, "model_generic_call: the callee failed");
        return NULL;
    }
    return model_nondet_object();
}
