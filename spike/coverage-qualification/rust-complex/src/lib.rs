//! Second Rust qualification sample for cic-branch/1: nested and compound constructs. NOT production code.
//! The hand-counted branches are in the table at the end of the file.

pub fn else_if(n: i32) -> &'static str {
    if n < 0 {
        "neg"
    } else if n == 0 {
        "zero"
    } else if n < 10 {
        "small"
    } else {
        "big"
    }
}

/// The labeled break leaves both loops: the outer loop then has no exhaustion exit.
pub fn find(m: &[Vec<i32>], t: i32) -> bool {
    let mut found = false;
    'outer: for row in m {
        for v in row {
            if *v == t {
                found = true;
                break 'outer;
            }
        }
    }
    found
}

pub fn arms(n: i32) -> i32 {
    match n {
        0 => {
            let a = 1;
            a + 1
        }
        1 | 2 => n * 10,
        x if x > 100 => {
            if x > 200 {
                3
            } else {
                4
            }
        }
        _ => match n % 2 {
            0 => 5,
            _ => 6,
        },
    }
}

/// `while let` left by break and passed over by continue.
pub fn drain(mut v: Vec<i32>) -> i32 {
    let mut s = 0;
    while let Some(x) = v.pop() {
        if x < 0 {
            continue;
        }
        if x > 100 {
            break;
        }
        s += x;
    }
    s
}

pub fn quest2(a: &str, b: &str) -> Result<i32, std::num::ParseIntError> {
    let x = a.parse::<i32>()?;
    let y = b.parse::<i32>()? + 1;
    Ok(x + y)
}

pub fn first_even(v: &[i32]) -> Option<i32> {
    let f = v.first()?;
    if f % 2 == 0 {
        Some(*f)
    } else {
        None
    }
}

pub fn closure_cond(xs: &[i32]) -> i32 {
    let f = |x: i32| x > 0 && x % 2 == 0;
    let mut n = 0;
    for &x in xs {
        if f(x) {
            n += 1;
        }
    }
    n
}

/// One source decision, two instantiations: the counters live in the source, so there is one edge pair.
pub fn generic<T: PartialOrd + Copy>(a: T, b: T) -> T {
    if a < b {
        b
    } else {
        a
    }
}

pub fn iflet_chain(o: Option<i32>, p: Option<i32>) -> i32 {
    if let Some(v) = o {
        v
    } else if let Some(w) = p {
        w
    } else {
        0
    }
}

pub fn loop_break(limit: u32) -> u32 {
    let mut i = 0;
    loop {
        i += 1;
        if i >= limit {
            break;
        }
    }
    i
}

pub fn nested_cond(a: bool, b: bool, c: bool) -> i32 {
    if a && (b || c) {
        1
    } else {
        2
    }
}

pub fn for_continue(v: &[i32]) -> i32 {
    let mut s = 0;
    for x in v {
        if *x % 2 == 0 {
            continue;
        }
        s += *x;
    }
    s
}

pub fn ret_early(x: Option<i32>) -> i32 {
    match x {
        Some(v) if v > 5 => return v,
        Some(v) => v + 1,
        None => 0,
    }
}

// Hand-counted branches (cic-branch/1): total / covered by the full tests / covered by the reduced tests
//   else_if      conditional 6 / 6 / 4
//   find         loop 4 + conditional 2 = 6 / 6 / 5
//   arms         match 8 (4 arms + 2 guard + 2 inner arms) + conditional 2 = 10 / 10 / 5
//   drain        loop 2 + conditional 4 = 6 / 6 / 4
//   quest2       error_path 4 / 4 / 2
//   first_even   error_path 2 + conditional 2 = 4 / 4 / 3
//   closure_cond short_circuit 4 + loop 2 + conditional 2 = 8 / 8 / 7
//   generic      conditional 2 / 2 / 1
//   iflet_chain  conditional 4 / 4 / 3
//   loop_break   conditional 2 / 2 / 2   (the unconditional `loop` has no entry decision)
//   nested_cond  conditional 2 + short_circuit 6 = 8 / 8 / 6
//   for_continue loop 2 + conditional 2 = 4 / 4 / 3
//   ret_early    match 5 (3 arms + 2 guard) / 5 / 3
// Total 69 / 69 / 48.
