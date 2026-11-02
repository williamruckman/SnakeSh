/* Parse launch options without contacting a server, then inspect the key map. */
#include <stdio.h>
#include <freerdp/client/cmdline.h>
#include <freerdp/locale/keyboard.h>
#include <freerdp/scancode.h>
#include <freerdp/settings.h>

int main(int argc, char **argv)
{
    rdpSettings *settings = freerdp_settings_new(0);
    if (!settings)
        return 4;
    const int status = freerdp_client_settings_parse_command_line_arguments(settings, argc, argv, FALSE);
    if (status != 0)
    {
        printf("{\"parse_status\":%d}\n", status);
        freerdp_settings_free(settings);
        return 2;
    }
    const char *list = freerdp_settings_get_string(settings, FreeRDP_KeyboardRemappingList);
    FREERDP_REMAP_TABLE *table = freerdp_keyboard_remap_string_to_list(list);
    if (!table)
    {
        freerdp_settings_free(settings);
        return 3;
    }
    const DWORD left = freerdp_keyboard_remap_key(table, RDP_SCANCODE_LWIN);
    const DWORD right = freerdp_keyboard_remap_key(table, RDP_SCANCODE_RWIN);
    const int valid = left == RDP_SCANCODE_LCONTROL && right == RDP_SCANCODE_RCONTROL
        && freerdp_keyboard_remap_key(table, RDP_SCANCODE_KEY_C) == RDP_SCANCODE_KEY_C
        && freerdp_keyboard_remap_key(table, RDP_SCANCODE_KEY_V) == RDP_SCANCODE_KEY_V
        && freerdp_keyboard_remap_key(table, RDP_SCANCODE_LCONTROL) == RDP_SCANCODE_LCONTROL
        && freerdp_keyboard_remap_key(table, RDP_SCANCODE_RCONTROL) == RDP_SCANCODE_RCONTROL;
    printf("{\"parse_status\":0,\"left_command\":%u,\"right_command\":%u,\"valid\":%s}\n",
           (unsigned int)left, (unsigned int)right, valid ? "true" : "false");
    freerdp_keyboard_remap_free(table);
    freerdp_settings_free(settings);
    return valid ? 0 : 3;
}
