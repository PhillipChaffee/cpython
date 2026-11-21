#include "bodyless.c"

void harness(void)
{
    int v;
    __CPROVER_assume(0 <= v && v <= 10);
    (void)lookup(v);
}
