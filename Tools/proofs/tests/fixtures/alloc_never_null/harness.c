/* Every allocating model must be able to return NULL: each assertion below
 * must fail (test_model_alloc_can_fail). */
#include "models.h"

void harness(void)
{
    void *p = PyMem_Malloc(8);
    __CPROVER_assert(p != NULL, "PyMem_Malloc never fails");
    void *q = PyMem_Calloc(2, 4);
    __CPROVER_assert(q != NULL, "PyMem_Calloc never fails");
    void *r = PyMem_Realloc(NULL, 8);
    __CPROVER_assert(r != NULL, "PyMem_Realloc never fails");
    void *s = PyMem_RawMalloc(8);
    __CPROVER_assert(s != NULL, "PyMem_RawMalloc never fails");
    void *t = PyObject_Malloc(8);
    __CPROVER_assert(t != NULL, "PyObject_Malloc never fails");
    void *u = PyObject_Calloc(2, 4);
    __CPROVER_assert(u != NULL, "PyObject_Calloc never fails");
    void *w = PyObject_Realloc(NULL, 8);
    __CPROVER_assert(w != NULL, "PyObject_Realloc never fails");
    PyObject *g = _PyObject_GC_New(&PyTuple_Type);
    __CPROVER_assert(g != NULL, "_PyObject_GC_New never fails");
    PyVarObject *gv = _PyObject_GC_NewVar(&PyTuple_Type, 2);
    __CPROVER_assert(gv != NULL, "_PyObject_GC_NewVar never fails");
}
