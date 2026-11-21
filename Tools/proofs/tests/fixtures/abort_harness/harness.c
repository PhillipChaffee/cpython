#include "abort.c"

void harness(void)
{
    int v;
    __CPROVER_assume(0 <= v && v <= 10);
    int r = check_nonneg(v);
    __CPROVER_assert(r == v, "returns its argument");
}
