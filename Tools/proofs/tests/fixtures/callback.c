int provider(int v);

int use_provider(int v)
{
    return provider(v) + 1;
}
