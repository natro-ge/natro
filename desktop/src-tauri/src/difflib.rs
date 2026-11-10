//! How alike two strings are, as Python's difflib measures it (SequenceMatcher's ratio and
//! get_close_matches), so app names are matched as they were in the Python helper: the same
//! scores, the same ties.

use std::collections::HashMap;

/// difflib's SequenceMatcher(None, a, b).ratio(): twice the characters in matching blocks,
/// over both lengths. Not symmetric: a's characters are looked for in b.
pub fn ratio(a: &str, b: &str) -> f64 {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let length = a.len() + b.len();
    if length == 0 {
        return 1.0;
    }
    let b2j = positions(&b);
    let mut matches = 0;
    let mut queue = vec![(0, a.len(), 0, b.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest_match(&a, &b, &b2j, alo, ahi, blo, bhi);
        if k > 0 {
            matches += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    2.0 * matches as f64 / length as f64
}

/// difflib.get_close_matches: the possibilities at least cutoff alike to word, the most alike
/// first (ties: the later in Python's string order first), at most n.
pub fn close_matches(word: &str, possibilities: &[String], n: usize, cutoff: f64) -> Vec<String> {
    let mut scored: Vec<(f64, &String)> = possibilities
        .iter()
        .map(|x| (ratio(x, word), x))
        .filter(|(score, _)| *score >= cutoff)
        .collect();
    scored.sort_by(|(s1, x1), (s2, x2)| s2.total_cmp(s1).then_with(|| x2.cmp(x1)));
    scored.into_iter().take(n).map(|(_, x)| x.clone()).collect()
}

/// Where each character is in b. A character in more than 1% of a string of 200 or more is
/// left out ("popular", difflib's autojunk); matches grow over it afterwards.
fn positions(b: &[char]) -> HashMap<char, Vec<usize>> {
    let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, c) in b.iter().enumerate() {
        b2j.entry(*c).or_default().push(j);
    }
    if b.len() >= 200 {
        let most = b.len() / 100 + 1;
        b2j.retain(|_, places| places.len() <= most);
    }
    b2j
}

/// difflib's find_longest_match, with no junk: (i, j, size), the earliest of the longest.
fn longest_match(
    a: &[char],
    b: &[char],
    b2j: &HashMap<char, Vec<usize>>,
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> (usize, usize, usize) {
    let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
    let mut j2len: HashMap<usize, usize> = HashMap::new();
    for (i, c) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut new_j2len = HashMap::new();
        for &j in b2j.get(c).map(Vec::as_slice).unwrap_or_default() {
            if j < blo {
                continue;
            }
            if j >= bhi {
                break;
            }
            let k = j.checked_sub(1).and_then(|before| j2len.get(&before)).copied().unwrap_or(0) + 1;
            new_j2len.insert(j, k);
            if k > bestsize {
                (besti, bestj, bestsize) = (i + 1 - k, j + 1 - k, k);
            }
        }
        j2len = new_j2len;
    }
    // Over the popular characters, which weren't looked for.
    while besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1] {
        (besti, bestj, bestsize) = (besti - 1, bestj - 1, bestsize + 1);
    }
    while besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] == b[bestj + bestsize] {
        bestsize += 1;
    }
    (besti, bestj, bestsize)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Python 3.13's difflib.SequenceMatcher(None, a, b).ratio() for each.
    #[test]
    fn the_same_ratios_as_python() {
        for (a, b, python) in [
            ("calculater", "calculator", 0.9),
            ("spotify", "spotify widget", 0.6666666666666666),
            ("telegram", "steam", 0.6153846153846154),
            ("abcabc", "cab", 0.6666666666666666),
            ("visual studio", "visual studio code", 0.8387096774193549),
            ("qwerty", "ytrewq", 0.16666666666666666),
            ("ab ba", "ba ab", 0.4),
            ("xyzabcxyz", "abcxyzabc", 0.6666666666666666),
            ("microsoft edge", "edge", 0.4444444444444444),
            ("tom", "motmot", 0.4444444444444444),
            ("motmot", "tom", 0.2222222222222222),
            ("", "", 1.0),
            ("a", "", 0.0),
        ] {
            assert_eq!(ratio(a, b), python, "{a:?} {b:?}");
        }
        // Long enough for difflib's popular characters.
        let (a, b) = ("ab".repeat(150) + "xyz", "b".repeat(20) + &"a".repeat(200) + "xyz");
        assert_eq!(ratio(&a, &b), 0.011406844106463879);
    }

    #[test]
    fn close_matches_as_python_orders_them() {
        let names: Vec<String> =
            ["spotify", "spotify widget", "visual studio code", "google chrome", "calculator", "steam"]
                .map(String::from)
                .to_vec();
        assert_eq!(close_matches("telegram", &names, 5, 0.3), ["steam", "google chrome"]);
        assert_eq!(close_matches("spot", &names, 5, 0.3), ["spotify", "steam", "spotify widget"]);
        assert!(close_matches("zzz", &names, 5, 0.3).is_empty());
    }
}
