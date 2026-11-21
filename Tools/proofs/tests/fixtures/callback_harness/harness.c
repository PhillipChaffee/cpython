#include "callback.c"

/* Defined here, so CBMC does not treat it as body-less: a stub in disguise. */
int provider(int v)
{
    return v;
}

static int helper(int v)
{
    return v * 2;
}

void harness(void)
{
    int v;
    __CPROVER_assume(0 <= v && v <= 10);
    int r = use_provider(helper(v));
    __CPROVER_assert(r == 2 * v + 1, "provider result plus one");
}
