/* CBMC's own malloc can fail (run.CHECKS has --malloc-may-fail
 * --malloc-fail-null): the NULL branch below is reachable. */
#include <stdlib.h>
#include "loop.c"

void harness(void)
{
    int *a = malloc(4 * sizeof(int));
    if (a == NULL) {
        __CPROVER_assert(1, "the allocation can fail");
        return;
    }
    for (int i = 0; i < 4; i++)
        __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000);
    int s = sum(a, 4);
    __CPROVER_assert(-4000 <= s && s <= 4000, "sum bounded");
    free(a);
}
