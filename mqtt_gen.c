#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <pthread.h>
#include <mosquitto.h>
#include <gpiod.h>
#include <cjson/cJSON.h>
#include <time.h>

#define BROKER      "127.0.0.1"
#define PORT        1883
#define TOPIC       "gpio/control"

static volatile int keep_running = 1;
static volatile int wave_active = 0;
static struct gpiod_line_request *req = NULL;
static pthread_t wave_thread = 0;
static int current_offset = 144;
static float current_freq = 1000.0f;
static float current_duty = 50.0f;

static void cleanup_gpio(void) {
    if (req) {
        gpiod_line_request_set_value(req, current_offset, 0);
        gpiod_line_request_release(req);
        req = NULL;
    }
}

static void signal_handler(int sig) {
    keep_running = 0;
    wave_active = 0;
}

static void sleep_ns(uint64_t ns) {
    if (ns == 0) return;
    struct timespec ts;
    ts.tv_sec = ns / 1000000000ULL;
    ts.tv_nsec = ns % 1000000000ULL;
    nanosleep(&ts, NULL);
}

static void* generate_square_wave(void* arg) {
    double period_sec = 1.0 / current_freq;
    uint64_t high_ns = (uint64_t)(period_sec * (current_duty / 100.0) * 1e9);
    uint64_t low_ns = (uint64_t)((period_sec * (1.0 - current_duty / 100.0)) * 1e9);

    printf("square wave: %.1f Hz on GPIO %d\n", current_freq, current_offset);

    while (wave_active && keep_running) {
        gpiod_line_request_set_value(req, current_offset, 1);
        sleep_ns(high_ns);

        gpiod_line_request_set_value(req, current_offset, 0);
        sleep_ns(low_ns);
    }

    if (req) gpiod_line_request_set_value(req, current_offset, 0);
    printf("Generator stopped.\n");
    return NULL;
}

static int setup_gpio(int offset) {
    struct gpiod_chip *chip;
    struct gpiod_line_settings *settings;
    struct gpiod_line_config *line_cfg;
    struct gpiod_request_config *req_cfg;
    cleanup_gpio();
    chip = gpiod_chip_open("/dev/gpiochip0");
    if (!chip) return -1;
    settings = gpiod_line_settings_new();
    gpiod_line_settings_set_direction(settings, GPIOD_LINE_DIRECTION_OUTPUT);
    line_cfg = gpiod_line_config_new();
    unsigned int off = (unsigned int)offset;
    gpiod_line_config_add_line_settings(line_cfg, &off, 1, settings);
    req_cfg = gpiod_request_config_new();
    gpiod_request_config_set_consumer(req_cfg, "wavegen_eco");
    req = gpiod_chip_request_lines(chip, req_cfg, line_cfg);
    gpiod_line_settings_free(settings);
    gpiod_line_config_free(line_cfg);
    gpiod_request_config_free(req_cfg);
    gpiod_chip_close(chip);
    return req ? 0 : -1;
}

static void on_message(struct mosquitto *mosq, void *userdata, const struct mosquitto_message *msg) {
    if (!msg->payload) return;
    cJSON *root = cJSON_Parse((const char*)msg->payload);
    if (!root) return;

    cJSON *action = cJSON_GetObjectItem(root, "action");
    if (cJSON_IsString(action)) {
        if (wave_active) {
            wave_active = 0;
            if (wave_thread) {
                pthread_join(wave_thread, NULL);
                wave_thread = 0;
            }
        }

        if (strcmp(action->valuestring, "stop") == 0) {
            printf("Stop signal received\n");
        }
        else if (strcmp(action->valuestring, "start") == 0) {
            cJSON *line_item = cJSON_GetObjectItem(root, "gpio_line");
            cJSON *freq_item = cJSON_GetObjectItem(root, "freq_hz");
            cJSON *duty_item = cJSON_GetObjectItem(root, "duty_percent");

            int new_line = line_item ? (int)line_item->valuedouble : 144;

            if (!req || new_line != current_offset) {
                setup_gpio(new_line);
                current_offset = new_line;
            }

            current_freq = freq_item ? (float)freq_item->valuedouble : 1000.0f;
            current_duty = duty_item ? (float)duty_item->valuedouble : 50.0f;

            wave_active = 1;
            if (pthread_create(&wave_thread, NULL, generate_square_wave, NULL) != 0) {
                perror("Failed to create thread");
                wave_active = 0;
            }
            printf("Restarted with: %.1f Hz, %.1f%% duty\n", current_freq, current_duty);
        }
    }
    cJSON_Delete(root);
}

int main() {
    signal(SIGINT, signal_handler);
    mosquitto_lib_init();
    struct mosquitto *mosq = mosquitto_new(NULL, true, NULL);
    mosquitto_message_callback_set(mosq, on_message);
    if (mosquitto_connect(mosq, BROKER, PORT, 60) != MOSQ_ERR_SUCCESS) return 1;
    mosquitto_subscribe(mosq, NULL, TOPIC, 1);
    printf("Connected to local MQTT broker. Topic: %s\n", TOPIC);
    while (keep_running) {
        mosquitto_loop(mosq, 100, 1);
    }
    wave_active = 0;
    cleanup_gpio();
    mosquitto_destroy(mosq);
    mosquitto_lib_cleanup();
    return 0;
}
