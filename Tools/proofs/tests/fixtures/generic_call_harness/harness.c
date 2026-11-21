/* model_generic_call(): NULL iff the error is set, else a new reference
 * (test_generic_call_null_implies_error_set). */
#include "models.h"

void harness(void)
{
    PyObject *r = model_generic_call();
    __CPROVER_assert((r == NULL) == model_error_is_set(), "NULL iff the error is set");
    if (r != NULL) {
        __CPROVER_assert(Py_REFCNT(r) >= 1, "a result is a live reference");
    }
    else {
        __CPROVER_assert(model_current_exception != NULL, "the call can fail");
    }
}
