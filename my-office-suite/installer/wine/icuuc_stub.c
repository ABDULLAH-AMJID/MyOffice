/*
 * icuuc_stub.c - stand-in for Windows' built-in icuuc.dll, used ONLY when building / testing the
 * Windows version of MyOffice under Wine on Linux (see installer/wine/README.md).
 *
 * Qt 6 (Qt6Core.dll) links against the ICU that ships with Windows 10 1703+ in System32, but only
 * for the 20 character-set converter functions below (QStringConverter, legacy encodings).  Wine
 * does not provide icuuc.dll, so Qt6Core.dll cannot load and PyInstaller cannot inspect PySide6.
 * This stub reports every converter as unavailable (exactly what Qt does when ICU lacks a codec);
 * UTF-8/16/32 and Latin-1 are handled by Qt itself and keep working.
 *
 * It is installed into the Wine prefix's System32 only - PyInstaller never bundles System32 DLLs,
 * so the real Windows icuuc.dll is used on Windows.  It is never part of MyOffice_Setup.exe.
 *
 *   x86_64-w64-mingw32-gcc -shared -O2 -s -o icuuc.dll icuuc_stub.c
 */
#include <stddef.h>
#include <stdint.h>

typedef int32_t UErrorCode;
typedef uint16_t UChar;
#define U_FILE_ACCESS_ERROR 4
#define EXPORT __declspec(dllexport)

static void fail(UErrorCode *err) { if (err && *err <= 0) *err = U_FILE_ACCESS_ERROR; }

EXPORT void *ucnv_open(const char *name, UErrorCode *err) { (void)name; fail(err); return NULL; }
EXPORT void ucnv_close(void *cnv) { (void)cnv; }
EXPORT void ucnv_reset(void *cnv) { (void)cnv; }
EXPORT int8_t ucnv_getMaxCharSize(const void *cnv) { (void)cnv; return 1; }
EXPORT const char *ucnv_getName(const void *cnv, UErrorCode *err) { (void)cnv; fail(err); return NULL; }
EXPORT const char *ucnv_getStandardName(const char *name, const char *standard, UErrorCode *err)
{ (void)name; (void)standard; fail(err); return NULL; }
EXPORT const char *ucnv_getAvailableName(int32_t n) { (void)n; return NULL; }
EXPORT int32_t ucnv_countAvailable(void) { return 0; }
EXPORT int32_t ucnv_toUCountPending(const void *cnv, UErrorCode *err) { (void)cnv; fail(err); return 0; }
EXPORT int32_t ucnv_fromUCountPending(const void *cnv, UErrorCode *err) { (void)cnv; fail(err); return 0; }

EXPORT void UCNV_FROM_U_CALLBACK_SUBSTITUTE(const void *ctx, void *args, const UChar *cu, int32_t len,
                                            int32_t cp, int32_t reason, UErrorCode *err)
{ (void)ctx; (void)args; (void)cu; (void)len; (void)cp; (void)reason; fail(err); }
EXPORT void UCNV_TO_U_CALLBACK_SUBSTITUTE(const void *ctx, void *args, const char *cb, int32_t len,
                                          int32_t reason, UErrorCode *err)
{ (void)ctx; (void)args; (void)cb; (void)len; (void)reason; fail(err); }

EXPORT void ucnv_getToUCallBack(const void *cnv, void **action, const void **ctx)
{ (void)cnv; if (action) *action = NULL; if (ctx) *ctx = NULL; }
EXPORT void ucnv_getFromUCallBack(const void *cnv, void **action, const void **ctx)
{ (void)cnv; if (action) *action = NULL; if (ctx) *ctx = NULL; }
EXPORT void ucnv_setToUCallBack(void *cnv, void *action, const void *ctx, void **old, const void **oldctx,
                                UErrorCode *err)
{ (void)cnv; (void)action; (void)ctx; if (old) *old = NULL; if (oldctx) *oldctx = NULL; fail(err); }
EXPORT void ucnv_setFromUCallBack(void *cnv, void *action, const void *ctx, void **old, const void **oldctx,
                                  UErrorCode *err)
{ (void)cnv; (void)action; (void)ctx; if (old) *old = NULL; if (oldctx) *oldctx = NULL; fail(err); }

EXPORT void ucnv_toUnicode(void *cnv, UChar **target, const UChar *targetLimit, const char **source,
                           const char *sourceLimit, int32_t *offsets, int8_t flush, UErrorCode *err)
{ (void)cnv; (void)target; (void)targetLimit; (void)source; (void)sourceLimit; (void)offsets; (void)flush; fail(err); }
EXPORT void ucnv_fromUnicode(void *cnv, char **target, const char *targetLimit, const UChar **source,
                             const UChar *sourceLimit, int32_t *offsets, int8_t flush, UErrorCode *err)
{ (void)cnv; (void)target; (void)targetLimit; (void)source; (void)sourceLimit; (void)offsets; (void)flush; fail(err); }
EXPORT void ucnv_cbToUWriteUChars(void *args, const UChar *source, int32_t length, int32_t offsetIndex,
                                  UErrorCode *err)
{ (void)args; (void)source; (void)length; (void)offsetIndex; fail(err); }
EXPORT void ucnv_cbFromUWriteUChars(void *args, const UChar **source, const UChar *sourceLimit,
                                    int32_t offsetIndex, UErrorCode *err)
{ (void)args; (void)source; (void)sourceLimit; (void)offsetIndex; fail(err); }
