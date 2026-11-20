//! Dependency-free Rust ports of two pieces of arm.py, to compare against the Python versions:
//!   armcore ik  chain.txt targets.txt reps   ElRobot damped-least-squares IK (same algorithm as elrobot/ik.py::_solve)
//!   armcore seg image.rgb roi.u8 W H reps    table-colour segmentation (same steps as Arm._blobs)
use std::time::Instant;

type V = [f64; 3];
type M = [[f64; 3]; 3];

fn mv(m: &M, v: V) -> V { [0, 1, 2].map(|i| m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2]) }
fn mm(a: &M, b: &M) -> M { let mut r = [[0.0; 3]; 3]; for i in 0..3 { for j in 0..3 { for k in 0..3 { r[i][j] += a[i][k] * b[k][j]; } } } r }
fn cross(a: V, b: V) -> V { [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]] }
fn add(a: V, b: V) -> V { [a[0] + b[0], a[1] + b[1], a[2] + b[2]] }
fn sub(a: V, b: V) -> V { [a[0] - b[0], a[1] - b[1], a[2] - b[2]] }
fn norm(a: V) -> f64 { (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt() }
const I3: M = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
const R_ROBOT: M = [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]];
const TOOL: V = [0.0, 0.10, 0.0];
const DOWN: V = [0.0, 0.0, -1.0];

fn quat(w: f64, x: f64, y: f64, z: f64) -> M {
    [[1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
     [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)],
     [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)]]
}
fn rodrigues(a: V, th: f64) -> M {
    let (s, c) = th.sin_cos();
    let k = [[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]];
    let kk = mm(&k, &k);
    let mut r = I3;
    for i in 0..3 { for j in 0..3 { r[i][j] += s * k[i][j] + (1.0 - c) * kk[i][j]; } }
    r
}

struct Link { p: V, r: M, axis: V, lo: f64, hi: f64 }

fn read_chain(path: &str) -> Vec<Link> {
    std::fs::read_to_string(path).unwrap().lines().filter(|l| !l.trim().is_empty()).map(|l| {
        let v: Vec<f64> = l.split_whitespace().map(|x| x.parse().unwrap()).collect();
        Link { p: [v[0], v[1], v[2]], r: quat(v[3], v[4], v[5], v[6]), axis: [v[7], v[8], v[9]], lo: v[10], hi: v[11] }
    }).collect()
}

/// Joint origins and axes (robot frame), tip position, approach axis (body y).
fn fk(ch: &[Link], q: &[f64]) -> (Vec<V>, Vec<V>, V, V) {
    let (mut r, mut p) = (I3, [0.0; 3]);
    let (mut os, mut ax) = (vec![], vec![]);
    for (l, qd) in ch.iter().zip(q) {
        p = add(p, mv(&r, l.p));
        r = mm(&r, &l.r);
        os.push(mv(&R_ROBOT, p));
        ax.push(mv(&R_ROBOT, mv(&r, l.axis)));
        r = mm(&r, &rodrigues(l.axis, qd.to_radians()));
    }
    let tip = mv(&R_ROBOT, add(p, mv(&r, TOOL)));
    let a = mv(&R_ROBOT, mv(&r, [0.0, 1.0, 0.0]));
    (os, ax, tip, a)
}

fn solve6(a: &mut [[f64; 6]; 6], b: &mut [f64; 6]) -> [f64; 6] {
    for i in 0..6 {
        let mut piv = i;
        for r in i + 1..6 { if a[r][i].abs() > a[piv][i].abs() { piv = r; } }
        a.swap(i, piv); b.swap(i, piv);
        for r in i + 1..6 {
            let f = a[r][i] / a[i][i];
            for c in i..6 { a[r][c] -= f * a[i][c]; }
            b[r] -= f * b[i];
        }
    }
    let mut x = [0.0; 6];
    for i in (0..6).rev() {
        let mut s = b[i];
        for c in i + 1..6 { s -= a[i][c] * x[c]; }
        x[i] = s / a[i][i];
    }
    x
}

fn ik(ch: &[Link], target: V, seed: &[f64]) -> Option<Vec<f64>> {
    let mut q = seed.to_vec();
    let lo: Vec<f64> = ch.iter().map(|l| l.lo).collect();
    let hi: Vec<f64> = ch.iter().map(|l| l.hi).collect();
    for _ in 0..300 {
        let (os, ax, tip, a) = fk(ch, &q);
        let ep = sub(target, tip);
        let er = cross(a, DOWN).map(|v| -0.05 * v);
        let err = [ep[0], ep[1], ep[2], er[0], er[1], er[2]];
        if err.iter().map(|v| v * v).sum::<f64>().sqrt() < 1e-4 { break; }
        let mut j = [[0.0; 6]; 6]; // rows: 3 position, 3 orientation; cols: joints 0..5
        for c in 0..6 {
            let jp = cross(ax[c], sub(tip, os[c]));
            let jo = cross(cross(ax[c], a), DOWN).map(|v| 0.05 * v);
            for r in 0..3 { j[r][c] = jp[r]; j[r + 3][c] = jo[r]; }
        }
        let mut free = [true; 6];
        let mut nxt = [0.0; 6];
        for _ in 0..6 {
            let mut jf = j;
            for r in 0..6 { for c in 0..6 { if !free[c] { jf[r][c] = 0.0; } } }
            let mut m = [[0.0; 6]; 6];
            for r in 0..6 { for c in 0..6 { for k in 0..6 { m[r][c] += jf[r][k] * jf[c][k]; } } m[r][r] += 1e-4; }
            let mut b = err;
            let y = solve6(&mut m, &mut b);
            let mut pinned = false;
            for c in 0..6 {
                let dq = (0..6).map(|r| jf[r][c] * y[r]).sum::<f64>().to_degrees();
                nxt[c] = q[c] + dq;
                if free[c] && ((nxt[c] > hi[c] && q[c] >= hi[c] - 1e-6) || (nxt[c] < lo[c] && q[c] <= lo[c] + 1e-6)) { free[c] = false; pinned = true; }
            }
            if !pinned { break; }
        }
        for c in 0..6 { q[c] = nxt[c].clamp(lo[c], hi[c]); }
    }
    let (_, _, tip, a) = fk(ch, &q);
    if norm(sub(target, tip)) > 0.005 || norm(cross(a, DOWN)) > 0.05 { None } else { Some(q) }
}

fn cmd_ik(args: &[String]) {
    let ch = read_chain(&args[0]);
    let nums: Vec<f64> = std::fs::read_to_string(&args[1]).unwrap().split_whitespace().map(|x| x.parse().unwrap()).collect();
    let reps: usize = args[2].parse().unwrap();
    let rows: Vec<&[f64]> = nums.chunks(3 + ch.len()).collect(); // target xyz + seed deg
    let mut out = vec![];
    let t = Instant::now();
    for rep in 0..reps {
        for r in &rows {
            let res = ik(&ch, [r[0], r[1], r[2]], &r[3..]);
            if rep == 0 { out.push(res); }
        }
    }
    let ms = t.elapsed().as_secs_f64() * 1000.0 / (reps * rows.len()) as f64;
    for res in out {
        match res { Some(q) => println!("1 {}", q.iter().map(|v| format!("{v:.6}")).collect::<Vec<_>>().join(" ")), None => println!("0") }
    }
    eprintln!("rust ik: {ms:.4} ms per solve ({} solves x {reps})", rows.len());
}

// ---- segmentation ----
fn srgb_lin(c: f64) -> f64 { if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) } }
fn lab(r: u8, g: u8, b: u8, lut: &[f64; 256]) -> [f32; 3] {
    let (r, g, b) = (lut[r as usize], lut[g as usize], lut[b as usize]);
    let x = (0.412453 * r + 0.357580 * g + 0.180423 * b) / 0.950456;
    let y = 0.212671 * r + 0.715160 * g + 0.072169 * b;
    let z = (0.019334 * r + 0.119193 * g + 0.950227 * b) / 1.088754;
    let f = |t: f64| if t > 0.008856 { t.cbrt() } else { 7.787 * t + 16.0 / 116.0 };
    let (fx, fy, fz) = (f(x), f(y), f(z));
    let l = if y > 0.008856 { 116.0 * fy - 16.0 } else { 903.3 * y };
    [(l * 255.0 / 100.0) as f32, (500.0 * (fx - fy) + 128.0) as f32, (200.0 * (fy - fz) + 128.0) as f32]
}
fn morph(m: &[u8], w: usize, h: usize, k: usize, erode: bool) -> Vec<u8> {
    let r = (k / 2) as isize;
    let mut o = vec![0u8; w * h];
    for y in 0..h as isize { for x in 0..w as isize {
        let mut v = erode as u8;
        'a: for dy in -r..=r { for dx in -r..=r {
            let (yy, xx) = (y + dy, x + dx);
            let s = if yy < 0 || xx < 0 || yy >= h as isize || xx >= w as isize { erode as u8 } else { m[yy as usize * w + xx as usize] };
            if erode && s == 0 { v = 0; break 'a; }
            if !erode && s == 1 { v = 1; break 'a; }
        } }
        o[y as usize * w + x as usize] = v;
    } }
    o
}

fn cmd_seg(args: &[String]) {
    let img = std::fs::read(&args[0]).unwrap();
    let roi = std::fs::read(&args[1]).unwrap();
    let (w, h): (usize, usize) = (args[2].parse().unwrap(), args[3].parse().unwrap());
    let reps: usize = args[4].parse().unwrap();
    let lut: [f64; 256] = std::array::from_fn(|i| srgb_lin(i as f64 / 255.0));
    let mut report = String::new();
    let t = Instant::now();
    for rep in 0..reps {
        let labs: Vec<[f32; 3]> = (0..w * h).map(|i| lab(img[3 * i], img[3 * i + 1], img[3 * i + 2], &lut)).collect();
        let mut med = [0f32; 3];
        for c in 0..3 {
            let mut v: Vec<f32> = (0..w * h).filter(|&i| roi[i] > 0).map(|i| labs[i][c]).collect();
            let n = v.len();
            v.select_nth_unstable_by(n / 2, |a, b| a.partial_cmp(b).unwrap());
            let hi = v[n / 2];
            let lo = if n % 2 == 0 { v[..n / 2].iter().cloned().fold(f32::MIN, f32::max) } else { hi };
            med[c] = (lo + hi) / 2.0;
        }
        let mask: Vec<u8> = (0..w * h).map(|i| {
            let d = [labs[i][0] - med[0], labs[i][1] - med[1], labs[i][2] - med[2]];
            (roi[i] > 0 && ((0.5 * d[0]).powi(2) + d[1].powi(2) + d[2].powi(2)).sqrt() > 14.0) as u8
        }).collect();
        let m = morph(&morph(&mask, w, h, 3, true), w, h, 3, false); // open 3x3
        let m = morph(&morph(&m, w, h, 5, false), w, h, 5, true); // close 5x5
        let mut seen = vec![false; w * h];
        let mut comps = vec![];
        for s in 0..w * h {
            if m[s] == 0 || seen[s] { continue; }
            let (mut stack, mut n, mut sx, mut sy) = (vec![s], 0usize, 0f64, 0f64);
            seen[s] = true;
            while let Some(p) = stack.pop() {
                n += 1; sx += (p % w) as f64; sy += (p / w) as f64;
                let (px, py) = ((p % w) as isize, (p / w) as isize);
                for dy in -1..=1isize { for dx in -1..=1isize {
                    let (x, y) = (px + dx, py + dy);
                    if x < 0 || y < 0 || x >= w as isize || y >= h as isize { continue; }
                    let q = y as usize * w + x as usize;
                    if m[q] == 1 && !seen[q] { seen[q] = true; stack.push(q); }
                } }
            }
            if n >= 80 { comps.push((sx / n as f64, sy / n as f64, n)); }
        }
        if rep == 0 {
            report += &format!("median {:.3} {:.3} {:.3}\nmask_px {}\n", med[0], med[1], med[2], m.iter().map(|&v| v as usize).sum::<usize>());
            for (cx, cy, n) in comps { report += &format!("comp {cx:.2} {cy:.2} {n}\n"); }
        }
    }
    print!("{report}");
    eprintln!("rust seg: {:.3} ms per image ({reps} reps)", t.elapsed().as_secs_f64() * 1000.0 / reps as f64);
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a.get(1).map(|s| s.as_str()) {
        Some("ik") => cmd_ik(&a[2..]),
        Some("seg") => cmd_seg(&a[2..]),
        _ => eprintln!("usage: armcore ik chain targets reps | seg image.rgb roi.u8 W H reps"),
    }
}
