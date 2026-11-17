/* Minimal replacement for libmspack's `oabextract` tool.
 * Usage: oabextract <compressed.oab> <output.bin>
 * Exit code 0 on success, 1 on failure. */
#include <stdio.h>
#include <mspack.h>

int main(int argc, char **argv) {
    struct msoab_decompressor *oabd;
    int err;

    if (argc != 3) {
        fprintf(stderr, "Usage: %s <input.oab> <output.bin>\n", argv[0]);
        return 2;
    }
    MSPACK_SYS_SELFTEST(err);
    if (err) {
        fprintf(stderr, "libmspack self-test failed (%d)\n", err);
        return 1;
    }
    oabd = mspack_create_oab_decompressor(NULL);
    if (!oabd) {
        fprintf(stderr, "cannot create OAB decompressor\n");
        return 1;
    }
    err = oabd->decompress(oabd, argv[1], argv[2]);
    mspack_destroy_oab_decompressor(oabd);
    if (err != MSPACK_ERR_OK) {
        fprintf(stderr, "decompression failed (libmspack error %d)\n", err);
        return 1;
    }
    return 0;
}
