#include "callback.c"
#include "provider.h"

void harness(void)
{
    int v;
    __CPROVER_assume(0 <= v && v <= 10);
    int r = use_provider(v);
    __CPROVER_assert(r == v + 1, "provider result plus one");
}
