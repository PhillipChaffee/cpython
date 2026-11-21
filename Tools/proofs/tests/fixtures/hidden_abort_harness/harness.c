#include <stdlib.h>
#include "loop.c"

#define DIE_IF(c) if (c) exit(1)

void harness(void)
{
    int a[4];
    int n, m;
    if (m > 4) abort();
    DIE_IF(n < 0 || n > 4);
    for (int i = 0; i < 4; i++)
        __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000);
    int s = sum(a, n);
    __CPROVER_assert(m <= 4 && -4000 <= s && s <= 4000, "abort() and exit() bound m and n");
}
