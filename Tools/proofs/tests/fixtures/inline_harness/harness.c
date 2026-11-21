#include "use.c"

void harness(void)
{
    int v;
    __CPROVER_assume(-100 <= v && v <= 100);
    __CPROVER_assert(quad(v) == 4 * v, "quad is 4v");
}
