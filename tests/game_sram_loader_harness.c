/* A separate process with the game's Unicode import API surface. Its imports
 * lie wherever this build puts them, as in every other version of the game. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <setupapi.h>
__declspec(dllimport) DWORD WINAPI FbwfIsFilterEnabled(PBOOL current, PBOOL next);
#define CHECK(condition, code) do { if (!(condition)) ExitProcess(code); } while (0)

void mainCRTStartup(void) {
    GUID guid = {0xBDE06013, 0x8122, 0x11DB, {0x96,0xA5,0x00,0xE0,0x81,0x61,0x16,0x5F}};
    SP_DEVICE_INTERFACE_DATA item;
    BYTE details[64], request[9] = {0x40,0,0,3,0,0,'G','H','I'}, output[3];
    BOOL current, next;
    HDEVINFO set;
    HANDLE a, b;
    DWORD required, bytes;
    FbwfIsFilterEnabled(&current, &next);  /* the import through which the game loads the DLL */
    item.cbSize = sizeof(item);
    set = SetupDiGetClassDevsW(&guid, NULL, NULL, DIGCF_DEVICEINTERFACE);
    CHECK(set != INVALID_HANDLE_VALUE, 31);
    CHECK(SetupDiEnumDeviceInterfaces(set, NULL, &guid, 0, &item), 32);
    CHECK(!SetupDiGetDeviceInterfaceDetailW(set, &item, NULL, 0, &required, NULL) && required <= sizeof(details), 33);
    ((SP_DEVICE_INTERFACE_DETAIL_DATA_W *)details)->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W);
    CHECK(SetupDiGetDeviceInterfaceDetailW(set, &item,
        (SP_DEVICE_INTERFACE_DETAIL_DATA_W *)details, sizeof(details), &required, NULL), 34);
    a = CreateFileW((WCHAR *)(details + 4), GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(a != INVALID_HANDLE_VALUE, 35);
    b = CreateFileW((WCHAR *)(details + 4), GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(b != INVALID_HANDLE_VALUE, 36);
    CHECK(SetupDiDestroyDeviceInfoList(set), 37);
    CHECK(DeviceIoControl(a, 0x9C402000, request, 9, NULL, 0, &bytes, NULL), 38);
    CHECK(DeviceIoControl(b, 0x9C402004, request, 6, output, 3, &bytes, NULL) && bytes == 3, 39);
    CHECK(output[0] == 'G' && output[1] == 'H' && output[2] == 'I', 40);
    CHECK(CloseHandle(a) && CloseHandle(b), 41);
    ExitProcess(0);
}
