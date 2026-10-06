# Sensor de carbón con Soliphant FTM51 y Raspberry PLC 19R

Lectura, clasificación y registro de un detector de nivel de sólidos
**Endress+Hauser Soliphant M FTM51** con electrónica **FEM55**, conectado a un
PLC **Industrial Shields Raspberry PLC 19R V6**.

El FEM55 es un transmisor de dos hilos que comunica el estado mediante tres
niveles de corriente:

- Aproximadamente 16 mA.
- Aproximadamente 8 mA.
- Menos de 3,6 mA ante fallo de alimentación o del sensor.

El programa convierte la lectura analógica del PLC a voltaje y corriente,
filtra pequeñas variaciones, aplica histéresis, registra cada muestra en CSV y
genera una gráfica del ensayo.

## Hardware utilizado

- Endress+Hauser Soliphant M FTM51.
- Electrónica FEM55, alimentación de 11 a 35/36 Vcc según las condiciones de
  instalación.
- Industrial Shields Raspberry PLC 19R V6.
- Fuente SELV de 24 Vcc.
- Resistencia shunt de **510 ohm, 1 %, mínimo 0,25 W; recomendado 0,5 W**.

## Conexión

```text
+24 Vcc  ───────────── borne 2 (+) del FEM55

borne 1 (-) FEM55 ────┬──── entrada analógica I0.5 del PLC
                       │
                     510 ohm / 1 % / 0,5 W
                       │
0 Vcc de la fuente ────┴──── GND del PLC
```

La fuente de 24 V y el PLC deben compartir la referencia de 0 V en este
conexionado. Endress+Hauser especifica una fuente con separación galvánica
segura, por ejemplo SELV.

> [!WARNING]
> No usar una resistencia de 2 kohm. A 16 mA produciría aproximadamente 32 V,
> excedería la entrada de 0-10 V del PLC y la carga admisible del FEM55.
> Las instalaciones con clasificación Ex deben ser verificadas por personal
> habilitado y respetar las barreras y certificados aplicables.

## Valores esperados con 510 ohm

| Condición configurada | Corriente | Tensión aproximada |
|---|---:|---:|
| Sin carbón | 16 mA, ±5 % | 7,75 a 8,57 V |
| Carbón detectado | 8 mA, ±6 % | 3,84 a 4,32 V |
| Fallo | Menos de 3,6 mA | Menos de 1,84 V |

El significado físico de 8 y 16 mA depende del selector MIN/MAX del FEM55. El
programa está configurado para el comportamiento observado en el equipo
probado: **16 mA sin carbón y 8 mA con carbón**.

## Cómo clasifica la señal

La entrada analógica del PLC se interpreta como un ADC de 12 bits:

```text
voltaje = lectura_ADC × 10 / 4095
corriente_mA = voltaje × 1000 / resistencia_ohm
```

Los valores iniciales de clasificación son:

- `FALLO_SENSOR`: 5 mA o menos.
- `CARBON_DETECTADO`: 11 mA o menos.
- `SIN_CARBON`: 13 mA o más.
- Entre 11 y 13 mA se conserva el estado anterior, creando histéresis.

Además, el programa utiliza una mediana de cinco muestras y exige tres muestras
consecutivas antes de confirmar un estado. Con el intervalo predeterminado de
100 ms, esto evita falsos cambios por ruido. Para registrar transiciones con
mayor resolución puede usarse un intervalo de 50 ms.

En el ensayo realizado con este montaje se obtuvieron medianas de **15,56 mA
sin carbón** y **7,76 mA con carbón**, con una separación amplia respecto de los
umbrales.

## Requisitos de software

El PLC debe tener instalada la biblioteca de Industrial Shields
`python3-librpiplc`. El equipo probado utilizó la versión 3.0.0. Para generar
gráficas también se necesita Matplotlib.

En sistemas Debian preparados por Industrial Shields normalmente pueden
comprobarse así:

```bash
python3 -c "from librpiplc import rpiplc; print('librpiplc disponible')"
python3 -c "import matplotlib; print(matplotlib.__version__)"
```

## Prueba sin hardware

La simulación recorre los estados de 16 mA, 8 mA, fallo y recuperación sin
acceder a las entradas del PLC:

```bash
python3 carbon_sensor.py --simulate --duration 16 \
  --csv carbon_simulado.csv \
  --plot carbon_simulado.png
```

## Registro real

Antes de comenzar, confirmar que ningún otro programa esté utilizando el mismo
hardware del PLC. El registrador no acciona salidas ni relés.

Ejemplo de un ensayo de un minuto en `I0.5`, con una muestra cada 50 ms:

```bash
python3 carbon_sensor.py \
  --input I0.5 \
  --resistor 510 \
  --interval 0.05 \
  --duration 60 \
  --csv ensayo_real_60s.csv \
  --plot ensayo_real_60s.png
```

Si `--duration` vale `0`, el registro continúa hasta pulsar `Ctrl+C`.

## Datos generados

El CSV contiene:

- Fecha y hora de cada muestra.
- Tiempo transcurrido.
- Lectura ADC sin procesar.
- Voltaje calculado.
- Corriente filtrada en mA.
- Estado instantáneo.
- Estado estable confirmado.

La gráfica muestra la corriente contra el tiempo, las referencias nominales,
los niveles medianos realmente medidos, el umbral de fallo, la banda de
histéresis y las ventanas en las que se detectó carbón.

Los CSV y PNG se ignoran en Git por defecto para no publicar datos de operación.

## Opciones principales

```text
--input              I0.2, I0.3, I0.4 o I0.5
--resistor           Resistencia shunt en ohmios; predeterminado 510
--interval            Período de muestreo en segundos
--filter-samples      Tamaño de la ventana de mediana
--debounce-samples    Muestras consecutivas para confirmar un estado
--fault-max-ma        Umbral máximo de fallo
--carbon-max-ma       Umbral para entrar en carbón detectado
--clear-min-ma        Umbral para volver a sin carbón
--duration            Duración en segundos; 0 hasta Ctrl+C
--csv                 Ruta del CSV
--plot                Ruta del PNG
--simulate            Ejecuta sin tocar el hardware
```

Para ver todas las opciones:

```bash
python3 carbon_sensor.py --help
```

## Pruebas automáticas

```bash
python3 -m unittest -v test_carbon_sensor.py
```

Las pruebas verifican las conversiones eléctricas, los niveles nominales, la
histéresis, el antirrebote y la detección de fallo.

