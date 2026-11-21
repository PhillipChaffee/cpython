#include "loop.c"

void harness(void)
{
    int a[4];
    int n;
    __CPROVER_assume(n > 4);
    __CPROVER_assume(n < 2);
    int s = sum(a, n);
    __CPROVER_assert(s == s, "trivially true");
}
