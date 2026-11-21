#include <stdlib.h>

static void require(int ok)
{
    if (!ok)
        abort();
}

int clamp(int v)
{
    require(v > -1000);
    return v > 10 ? 10 : v;
}
