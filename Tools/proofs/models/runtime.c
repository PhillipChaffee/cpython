/* The runtime state that core code reads through globals.
 *
 * _PyRuntime holds the real small-int table (_Py_small_ints_INIT, the
 * initializer Python/pylifecycle.c uses through _PyRuntimeState_INIT), so
 * _PyLong_SMALL_INTS have exactly their real contents.  Every other field
 * is zero.  The full _PyRuntimeState_INIT is not used: it also builds the
 * static bytes and str singletons, whose initializers store (char)128..255
 * and fail --conversion-check in the model's own initialisation.  A harness
 * whose target reads another field of _PyRuntime or of the main interpreter
 * must set that field itself (nondeterministically, where a running
 * interpreter can hold any value).  Zero is the real initial value of the
 * fields the pilot's targets read besides these: the reference tracer
 * (_PyRuntime.ref_tracer, off) and the free lists (set by
 * model_init_runtime()).
 *
 * The current thread state (_Py_tss_tstate) and interpreter
 * (_Py_tss_interp) are the main interpreter of _PyRuntime and one thread
 * state of it.
 */
#include "Python.h"
#include "pycore_ceval.h"           // Py_DEFAULT_RECURSION_LIMIT
#include "pycore_freelist.h"        // struct _Py_freelist
#include "pycore_interp.h"          // _PyInterpreterState_WHENCE_NOTSET
#include "pycore_long.h"
#include "pycore_object.h"
#include "pycore_pystate.h"
#include "pycore_runtime.h"
#include "pycore_runtime_init.h"    // _PyRuntimeState_INIT
#include "pycore_tstate.h"          // _PyThreadStateImpl
#include "models.h"

_PyRuntimeState _PyRuntime = {
    .static_objects = { .singletons = { .small_ints = _Py_small_ints_INIT } },
};

static _PyThreadStateImpl model_tstate = {
    .base = { .interp = &_PyRuntime._main_interpreter },
};

_Py_thread_local PyThreadState *_Py_tss_tstate = &model_tstate.base;
_Py_thread_local PyInterpreterState *_Py_tss_interp = &_PyRuntime._main_interpreter;

/* Contract: the int free list (the only one the pilot's targets use) holds
 * nothing or one 1-digit int block, and its size is any value in [0,
 * Py_ints_MAXFREELIST], so both the push that stores and the push that
 * frees (list full) are possible.  The real list can hold up to
 * Py_ints_MAXFREELIST blocks; a pop never looks past the first. */
void
model_init_runtime(void)
{
    struct _Py_freelist *ints = &_PyRuntime._main_interpreter.object_state.freelists.ints;
    Py_ssize_t size;
    ints->size = (0 <= size && size <= Py_ints_MAXFREELIST) ? size : 0;
    int one;
    if (one && ints->size > 0) {
        PyLongObject *op = __CPROVER_allocate(sizeof(PyLongObject), 0);
        op->ob_base.ob_type = &PyLong_Type;
        *(void **)op = NULL;                /* end of the list */
        ints->freelist = op;
    }
    else {
        ints->freelist = NULL;
    }
}
