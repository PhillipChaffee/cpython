#include <stdlib.h>

int check_nonneg(int v)
{
    if (v < 0)
        abort();
    return v;
}
