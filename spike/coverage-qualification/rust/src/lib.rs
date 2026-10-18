//! Branch coverage qualification sample (cic-branch/1). NOT production code.
//! Every function carries its hand-counted branches in the table at the end of the file.

pub fn cond(x: i32) -> i32 {
    if x > 0 {
        1
    } else {
        2
    }
}

pub fn let_opt(o: Option<i32>) -> i32 {
    if let Some(v) = o {
        v
    } else {
        0
    }
}

pub fn m(n: i32) -> &'static str {
    match n {
        0 => "zero",
        x if x > 10 => "big",
        _ => "other",
    }
}

pub fn w(mut n: u32) -> u32 {
    let mut s = 0;
    while n > 0 {
        s += n;
        n -= 1;
    }
    s
}

pub fn wl(mut v: Vec<i32>) -> i32 {
    let mut s = 0;
    while let Some(x) = v.pop() {
        s += x;
    }
    s
}

pub fn fo(v: &[i32]) -> i32 {
    let mut s = 0;
    for x in v {
        s += *x;
    }
    s
}

pub fn lp() -> u32 {
    let mut i = 0;
    loop {
        i += 1;
        if i >= 3 {
            break;
        }
    }
    i
}

pub fn sc(a: bool, b: bool) -> bool {
    a && b
}

pub fn so(a: bool, b: bool) -> bool {
    a || b
}

pub fn q(s: &str) -> Result<i32, std::num::ParseIntError> {
    let n = s.parse::<i32>()?;
    Ok(n + 1)
}

// Hand-counted branches (cic-branch/1), total / covered by the full test set / covered by the reduced set:
//   cond    conditional 2 / 2 / 1
//   let_opt conditional 2 / 2 / 2
//   m       match: 3 arms + 2 guard outcomes = 5 / 5 / 3
//   w       loop 2 / 2 / 2
//   wl      loop 2 / 2 / 2
//   fo      loop 2 / 2 / 1
//   lp      conditional 2 / 2 / 2   (the unconditional `loop` has no entry decision)
//   sc      short_circuit 4 / 4 / 3
//   so      short_circuit 4 / 4 / 3
//   q       error_path 2 / 2 / 1
// Per category: conditional 6/6/5, match 5/5/3, loop 6/6/5, short_circuit 8/8/6, error_path 2/2/1.
