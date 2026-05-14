pip install PyQt6 paho-mqtt
gcc mqtt_wavegen.c -o mqtt_wavegen -lmosquitto -lgpiod -lcjson -lphtread
chrt -f 80 ./mqtt_wavegen
