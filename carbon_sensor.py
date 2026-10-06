#!/usr/bin/env python3
"""Monitor Endress+Hauser Soliphant FTM51/FEM55 on a Raspberry PLC 19R.

The FEM55 is a two-wire 8/16 mA point-level switch. A precision shunt
resistor converts loop current to the 0-10 V range accepted by the PLC.
This program never operates PLC outputs; it only reads one analog input.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


ADC_MAX_DEFAULT = 4095
ADC_RANGE_VOLTS = 10.0
VALID_INPUTS = ("I0.2", "I0.3", "I0.4", "I0.5")

UNKNOWN = "DESCONOCIDO"
TRANSITION = "TRANSICION"
FAULT = "FALLO_SENSOR"
CARBON = "CARBON_DETECTADO"
CLEAR = "SIN_CARBON"


@dataclass(frozen=True)
class Thresholds:
    """Current thresholds with a dead band between 11 and 13 mA."""

    fault_max_ma: float = 5.0
    carbon_max_ma: float = 11.0
    clear_min_ma: float = 13.0

    def validate(self) -> None:
        if not (0 < self.fault_max_ma < self.carbon_max_ma < self.clear_min_ma):
            raise ValueError("Los umbrales deben cumplir: 0 < fallo < carbon < libre")


def raw_to_volts(raw: int, adc_max: int = ADC_MAX_DEFAULT) -> float:
    if adc_max <= 0:
        raise ValueError("adc_max debe ser positivo")
    return max(0, raw) * ADC_RANGE_VOLTS / adc_max


def volts_to_ma(volts: float, resistor_ohms: float) -> float:
    if resistor_ohms <= 0:
        raise ValueError("La resistencia debe ser positiva")
    return volts * 1000.0 / resistor_ohms


def raw_to_ma(raw: int, resistor_ohms: float, adc_max: int = ADC_MAX_DEFAULT) -> float:
    return volts_to_ma(raw_to_volts(raw, adc_max), resistor_ohms)


def ma_to_raw(current_ma: float, resistor_ohms: float, adc_max: int = ADC_MAX_DEFAULT) -> int:
    volts = current_ma * resistor_ohms / 1000.0
    return round(volts * adc_max / ADC_RANGE_VOLTS)


class DebouncedClassifier:
    """Classify 8/16 mA levels using hysteresis and consecutive samples."""

    def __init__(self, thresholds: Thresholds, debounce_samples: int = 3) -> None:
        thresholds.validate()
        if debounce_samples < 1:
            raise ValueError("debounce_samples debe ser al menos 1")
        self.thresholds = thresholds
        self.debounce_samples = debounce_samples
        self.state = UNKNOWN
        self._candidate = UNKNOWN
        self._candidate_count = 0

    def instantaneous(self, current_ma: float) -> str:
        t = self.thresholds
        if current_ma <= t.fault_max_ma:
            return FAULT
        if current_ma <= t.carbon_max_ma:
            return CARBON
        if current_ma >= t.clear_min_ma:
            return CLEAR
        if self.state in (CARBON, CLEAR):
            return self.state
        return TRANSITION

    def update(self, current_ma: float) -> tuple[str, str]:
        candidate = self.instantaneous(current_ma)
        if candidate == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = candidate
            self._candidate_count = 1

        if candidate != TRANSITION and self._candidate_count >= self.debounce_samples:
            self.state = candidate
        return candidate, self.state


class RealPLCReader:
    def __init__(self, pin: str) -> None:
        from librpiplc import rpiplc

        self._plc = rpiplc
        self.pin = pin
        self._plc.init("RPIPLC_V6", "RPIPLC_19R")
        self._plc.pin_mode(pin, self._plc.INPUT)

    def read(self) -> int:
        value = int(self._plc.analog_read(self.pin))
        if value < 0:
            raise RuntimeError(f"Lectura ADC invalida: {value}")
        return value

    def close(self) -> None:
        self._plc.deinit()


class SimulatedReader:
    """Repeatable 16 -> 8 -> fault -> 16 mA sequence for offline testing."""

    def __init__(self, resistor_ohms: float, adc_max: int) -> None:
        self.resistor_ohms = resistor_ohms
        self.adc_max = adc_max
        self.started = time.monotonic()

    def read(self) -> int:
        elapsed = (time.monotonic() - self.started) % 16.0
        if elapsed < 4.0:
            current_ma = 16.0
        elif elapsed < 5.0:
            current_ma = 16.0 - 8.0 * (elapsed - 4.0)
        elif elapsed < 9.0:
            current_ma = 8.0
        elif elapsed < 11.0:
            current_ma = 3.2
        elif elapsed < 12.0:
            current_ma = 3.2 + 12.8 * (elapsed - 11.0)
        else:
            current_ma = 16.0
        current_ma += 0.10 * math.sin(elapsed * 13.0)
        return ma_to_raw(current_ma, self.resistor_ohms, self.adc_max)

    def close(self) -> None:
        pass


def default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.home() / "carbon_logs" / f"carbon_{stamp}.csv"


def create_plot(csv_path: Path, plot_path: Path, thresholds: Thresholds) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    elapsed: list[float] = []
    currents: list[float] = []
    states: list[str] = []
    with csv_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            elapsed.append(float(row["elapsed_s"]))
            currents.append(float(row["current_ma"]))
            states.append(row["stable_state"])

    if not elapsed:
        return

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(elapsed, currents, color="#145DA0", linewidth=1.5, label="Corriente FEM55")
    ax.axhline(16.0, color="#2E8B57", alpha=0.45, linestyle=":", label="Referencia: 16 mA")
    ax.axhline(8.0, color="#D97706", alpha=0.45, linestyle=":", label="Referencia: 8 mA")

    measured_clear = [
        current
        for current, state in zip(currents, states)
        if state == CLEAR and current >= thresholds.clear_min_ma
    ]
    measured_carbon = [
        current
        for current, state in zip(currents, states)
        if state == CARBON and thresholds.fault_max_ma < current <= thresholds.carbon_max_ma
    ]
    if measured_clear:
        clear_median = statistics.median(measured_clear)
        ax.axhline(
            clear_median,
            color="#15803D",
            linestyle="--",
            label=f"Medido sin carbon: {clear_median:.2f} mA",
        )
    if measured_carbon:
        carbon_median = statistics.median(measured_carbon)
        ax.axhline(
            carbon_median,
            color="#C2410C",
            linestyle="--",
            label=f"Medido con carbon: {carbon_median:.2f} mA",
        )

    carbon_start: Optional[float] = None
    for sample_time, state in zip(elapsed, states):
        if state == CARBON and carbon_start is None:
            carbon_start = sample_time
        elif state != CARBON and carbon_start is not None:
            ax.axvspan(carbon_start, sample_time, color="#F59E0B", alpha=0.08)
            carbon_start = None
    if carbon_start is not None:
        ax.axvspan(carbon_start, elapsed[-1], color="#F59E0B", alpha=0.08)
    ax.axhline(thresholds.fault_max_ma, color="#C62828", linestyle="--", label="Umbral de fallo")
    ax.axhspan(
        thresholds.carbon_max_ma,
        thresholds.clear_min_ma,
        color="#9CA3AF",
        alpha=0.18,
        label="Histeresis",
    )
    ax.set(title="Soliphant FTM51/FEM55 - cambio de estado", xlabel="Tiempo (s)", ylabel="Corriente (mA)")
    ax.grid(True, alpha=0.25)
    ax.set_ylim(bottom=0)
    ax.legend(loc="best")
    fig.tight_layout()
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Registro del Soliphant FTM51/FEM55")
    parser.add_argument("--input", choices=VALID_INPUTS, default="I0.5", help="entrada analogica")
    parser.add_argument("--resistor", type=float, default=510.0, help="resistencia shunt en ohmios")
    parser.add_argument("--adc-max", type=int, default=ADC_MAX_DEFAULT, help="cuenta ADC correspondiente a 10 V")
    parser.add_argument("--interval", type=float, default=0.10, help="periodo entre muestras en segundos")
    parser.add_argument("--filter-samples", type=int, default=5, help="ventana de mediana")
    parser.add_argument("--debounce-samples", type=int, default=3, help="muestras consecutivas para cambiar estado")
    parser.add_argument("--fault-max-ma", type=float, default=5.0)
    parser.add_argument("--carbon-max-ma", type=float, default=11.0)
    parser.add_argument("--clear-min-ma", type=float, default=13.0)
    parser.add_argument("--duration", type=float, default=0.0, help="segundos; 0 significa hasta Ctrl+C")
    parser.add_argument("--csv", type=Path, default=None, help="archivo CSV de salida")
    parser.add_argument("--plot", type=Path, default=None, help="PNG que se genera al terminar")
    parser.add_argument("--simulate", action="store_true", help="probar sin tocar el hardware")
    return parser


def monitor(args: argparse.Namespace) -> tuple[Path, Optional[Path]]:
    if args.resistor <= 0 or args.interval <= 0 or args.filter_samples < 1:
        raise ValueError("Resistencia, intervalo y filtro deben ser positivos")
    if args.adc_max <= 0:
        raise ValueError("adc-max debe ser positivo")

    max_nominal_v = 16.8 * args.resistor / 1000.0
    if max_nominal_v > ADC_RANGE_VOLTS:
        raise ValueError(
            f"Resistencia insegura para la entrada 0-10 V: a 16,8 mA produciria {max_nominal_v:.2f} V"
        )

    thresholds = Thresholds(args.fault_max_ma, args.carbon_max_ma, args.clear_min_ma)
    classifier = DebouncedClassifier(thresholds, args.debounce_samples)
    window: deque[float] = deque(maxlen=args.filter_samples)
    csv_path = args.csv or default_output_path()
    plot_path = args.plot
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    reader = SimulatedReader(args.resistor, args.adc_max) if args.simulate else RealPLCReader(args.input)
    started = time.monotonic()
    next_sample = started
    previous_state = UNKNOWN

    print(f"Entrada={args.input}  R={args.resistor:g} ohm  CSV={csv_path}")
    print("Ctrl+C para terminar")

    try:
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                ("timestamp", "elapsed_s", "raw", "volts", "current_ma", "instant_state", "stable_state")
            )
            while True:
                now = time.monotonic()
                if args.duration > 0 and now - started >= args.duration:
                    break
                if now < next_sample:
                    time.sleep(next_sample - now)
                captured = time.monotonic()
                raw = reader.read()
                volts = raw_to_volts(raw, args.adc_max)
                current_ma = volts_to_ma(volts, args.resistor)
                window.append(current_ma)
                filtered_ma = statistics.median(window)
                instant, stable = classifier.update(filtered_ma)
                elapsed = captured - started
                writer.writerow(
                    (
                        datetime.now().isoformat(timespec="milliseconds"),
                        f"{elapsed:.3f}",
                        raw,
                        f"{volts:.4f}",
                        f"{filtered_ma:.4f}",
                        instant,
                        stable,
                    )
                )
                stream.flush()
                if stable != previous_state:
                    print(f"{elapsed:8.2f}s  {volts:6.3f} V  {filtered_ma:6.2f} mA  -> {stable}")
                    previous_state = stable
                next_sample += args.interval
    except KeyboardInterrupt:
        print("\nRegistro detenido por el operador")
    finally:
        reader.close()

    if plot_path is not None:
        create_plot(csv_path, plot_path, thresholds)
    return csv_path, plot_path


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        csv_path, plot_path = monitor(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"CSV guardado: {csv_path}")
    if plot_path is not None:
        print(f"Grafico guardado: {plot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
