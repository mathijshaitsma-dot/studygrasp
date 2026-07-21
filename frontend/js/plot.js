// Veilige wiskundige-expressie-evaluator voor grafieken (kind:"function").
// Géén eval: een eigen tokenizer + shunting-yard + RPN-uitvoering met een
// strikte whitelist van operatoren/functies. Zo is de grafiek écht berekend
// (deterministisch, correct) i.p.v. door de AI geraden, en toch veilig.

const FUNCS = {
  sin: Math.sin, cos: Math.cos, tan: Math.tan,
  asin: Math.asin, acos: Math.acos, atan: Math.atan,
  sqrt: Math.sqrt, exp: Math.exp, abs: Math.abs,
  log: Math.log10 || ((x) => Math.log(x) / Math.LN10), // "log" = log10 (gangbaar op school)
  ln: Math.log,
};
const CONSTS = { pi: Math.PI, e: Math.E };
const OPS = {
  "+": { prec: 2, assoc: "L", fn: (a, b) => a + b },
  "-": { prec: 2, assoc: "L", fn: (a, b) => a - b },
  "*": { prec: 3, assoc: "L", fn: (a, b) => a * b },
  "/": { prec: 3, assoc: "L", fn: (a, b) => a / b },
  "^": { prec: 4, assoc: "R", fn: (a, b) => Math.pow(a, b) },
};

function tokenize(src) {
  const tokens = [];
  let i = 0;
  const s = src.replace(/\s+/g, "");
  while (i < s.length) {
    const c = s[i];
    if (/[0-9.]/.test(c)) {
      let num = "";
      while (i < s.length && /[0-9.]/.test(s[i])) num += s[i++];
      tokens.push({ t: "num", v: parseFloat(num) });
    } else if (/[a-z]/i.test(c)) {
      let name = "";
      while (i < s.length && /[a-z]/i.test(s[i])) name += s[i++].toLowerCase();
      if (name in FUNCS) tokens.push({ t: "func", v: name });
      else if (name in CONSTS) tokens.push({ t: "num", v: CONSTS[name] });
      else if (name === "x") tokens.push({ t: "var" });
      else throw new Error(`onbekend symbool: ${name}`);
    } else if (c in OPS) {
      // unaire min/plus → 0 - x
      const prev = tokens[tokens.length - 1];
      const unary = (c === "-" || c === "+") &&
        (!prev || prev.t === "op" || prev.t === "lparen" || prev.t === "comma");
      if (unary) tokens.push({ t: "num", v: 0 });
      tokens.push({ t: "op", v: c });
      i++;
    } else if (c === "(") { tokens.push({ t: "lparen" }); i++; }
    else if (c === ")") { tokens.push({ t: "rparen" }); i++; }
    else throw new Error(`onbekend teken: ${c}`);
  }
  return tokens;
}

function toRPN(tokens) {
  const out = [];
  const stack = [];
  for (const tok of tokens) {
    if (tok.t === "num" || tok.t === "var") out.push(tok);
    else if (tok.t === "func") stack.push(tok);
    else if (tok.t === "op") {
      while (stack.length) {
        const top = stack[stack.length - 1];
        if (top.t === "op" && (
          (OPS[tok.v].assoc === "L" && OPS[tok.v].prec <= OPS[top.v].prec) ||
          (OPS[tok.v].assoc === "R" && OPS[tok.v].prec < OPS[top.v].prec))) {
          out.push(stack.pop());
        } else break;
      }
      stack.push(tok);
    } else if (tok.t === "lparen") stack.push(tok);
    else if (tok.t === "rparen") {
      while (stack.length && stack[stack.length - 1].t !== "lparen") out.push(stack.pop());
      if (!stack.length) throw new Error("ongebalanceerde haakjes");
      stack.pop();
      if (stack.length && stack[stack.length - 1].t === "func") out.push(stack.pop());
    }
  }
  while (stack.length) {
    const top = stack.pop();
    if (top.t === "lparen") throw new Error("ongebalanceerde haakjes");
    out.push(top);
  }
  return out;
}

// Compileert een expressie naar een functie f(x). Gooit bij ongeldige invoer.
export function compile(expr) {
  const rpn = toRPN(tokenize(String(expr)));
  return (x) => {
    const st = [];
    for (const tok of rpn) {
      if (tok.t === "num") st.push(tok.v);
      else if (tok.t === "var") st.push(x);
      else if (tok.t === "func") st.push(FUNCS[tok.v](st.pop()));
      else if (tok.t === "op") { const b = st.pop(), a = st.pop(); st.push(OPS[tok.v].fn(a, b)); }
    }
    if (st.length !== 1) throw new Error("ongeldige expressie");
    return st[0];
  };
}

// Evalueert expr over [min,max] in `samples` punten → [{x,y}], oneindige/NaN
// waarden (bv. asymptoten) worden overgeslagen zodat de grafiek niet ontploft.
export function evaluateFunction(expr, domain, samples = 160) {
  const f = compile(expr);
  const [min, max] = domain;
  const pts = [];
  const step = (max - min) / (samples - 1);
  for (let i = 0; i < samples; i++) {
    const x = min + i * step;
    let y;
    try { y = f(x); } catch { y = NaN; }
    pts.push({ x, y: Number.isFinite(y) ? y : null });
  }
  return pts;
}
