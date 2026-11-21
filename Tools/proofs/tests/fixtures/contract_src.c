/* Functions for the contract fixtures: add_into() has an enforced
 * contract (fixture_contracts/add_into.h), sub_from() a contract that no
 * harness enforces (fixture_contracts/orphan.h). */
static int
add_into(int *x, int v)
{
    *x += v;
    return *x;
}

static int
sub_from(int *x, int v)
{
    *x -= v;
    return *x;
}

int
add_three(int *x)
{
    add_into(x, 1);
    return add_into(x, 2);
}

int
sub_three(int *x)
{
    return sub_from(x, 3);
}
