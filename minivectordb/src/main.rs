//! MiniVectorDB: JSONL stdio protocol server.

mod engine;
mod json;
mod sql;

use json::Json;
use std::io::{BufRead, Write};

fn respond(out: &mut impl Write, value: Json) {
    let text = json::to_string(&value);
    let _ = out.write_all(text.as_bytes());
    let _ = out.write_all(b"\n");
    let _ = out.flush();
}

fn ok_ok(id: &Json) -> Json {
    Json::Obj(vec![("id".into(), id.clone()), ("ok".into(), Json::Bool(true))])
}

fn main() {
    let data_dir = std::env::var("MVDB_DATA")
        .or_else(|_| std::env::var("VECTORDB_DATA_DIR"))
        .unwrap_or_else(|_| "/data".to_string());
    let mut db = match engine::Db::open(&data_dir) {
        Ok(db) => db,
        Err(e) => {
            eprintln!("failed to open database: {} ({})", e.message, e.code);
            std::process::exit(1);
        }
    };
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    let stdin = std::io::stdin();
    // handshake
    respond(&mut out, Json::Obj(vec![("type".into(), Json::Str("ready".into()))]));
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let req = match json::parse(&line) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("bad request: {}", e);
                continue;
            }
        };
        let id = req.get("id").cloned().unwrap_or(Json::Null);
        let op = req.get("op").and_then(|v| v.as_str()).unwrap_or("");
        match op {
            "execute" => {
                let mode = req.get("search_mode").and_then(|v| v.as_str()).unwrap_or("exact");
                db.search_mode = mode.to_string();
                let sql_text = req.get("sql").and_then(|v| v.as_str()).unwrap_or("");
                let result = sql::Parser::new(sql_text).and_then(|mut p| p.parse_stmt());
                match result {
                    Ok(stmt) => match db.execute(&stmt) {
                        Ok(engine::ExecResult::Ok) => respond(&mut out, ok_ok(&id)),
                        Ok(engine::ExecResult::Affected(n)) => respond(
                            &mut out,
                            Json::Obj(vec![
                                ("id".into(), id),
                                ("ok".into(), Json::Bool(true)),
                                ("affected_rows".into(), Json::Int(n)),
                            ]),
                        ),
                        Ok(engine::ExecResult::Rows(cols, rows)) => {
                            let columns = Json::Arr(cols.into_iter().map(Json::Str).collect());
                            let rows = Json::Arr(rows.into_iter().map(Json::Arr).collect());
                            respond(
                                &mut out,
                                Json::Obj(vec![
                                    ("id".into(), id),
                                    ("ok".into(), Json::Bool(true)),
                                    ("columns".into(), columns),
                                    ("rows".into(), rows),
                                ]),
                            );
                        }
                        Err(e) => error_response(&mut out, id, &e.code, &e.message),
                    },
                    Err(e) => {
                        let code = if e == "unsupported_sql" { "unsupported_sql" } else { "syntax_error" };
                        error_response(&mut out, id, code, &e);
                    }
                }
            }
            "shutdown" => {
                if let Err(e) = db.shutdown() {
                    error_response(&mut out, id.clone(), &e.code, &e.message);
                } else {
                    respond(&mut out, ok_ok(&id));
                }
                return;
            }
            other => {
                error_response(&mut out, id, "unsupported_op", &format!("unknown op {}", other));
            }
        }
    }
}

fn error_response(out: &mut impl Write, id: Json, code: &str, message: &str) {
    respond(
        out,
        Json::Obj(vec![
            ("id".into(), id),
            ("ok".into(), Json::Bool(false)),
            (
                "error".into(),
                Json::Obj(vec![
                    ("code".into(), Json::Str(code.to_string())),
                    ("message".into(), Json::Str(message.to_string())),
                ]),
            ),
        ]),
    );
}
