from dataclasses import dataclass


@dataclass(frozen=True)
class CalculationResult:
    value: float
    symbol: str


def calculate(a: float, operator: str, b: float) -> CalculationResult:
    normalized_operator = operator.lower()

    if normalized_operator in ["+", "add", "soma"]:
        return CalculationResult(a + b, "+")

    if normalized_operator in ["-", "sub", "menos"]:
        return CalculationResult(a - b, "-")

    if normalized_operator in ["*", "x", "mul", "vezes"]:
        return CalculationResult(a * b, "*")

    if normalized_operator in ["/", "div", "divide"]:
        if b == 0:
            raise ValueError("Nao e possivel dividir por zero.")
        return CalculationResult(a / b, "/")

    raise ValueError("Use um operador valido: +, -, *, /")
