//! Minimal SQL lexer/parser for the MiniVectorDB subset.

#[derive(Debug, Clone, PartialEq)]
pub enum ColType {
    Bigint,
    Varchar(usize),
    Vector(usize),
}

#[derive(Debug, Clone)]
pub struct ColumnDef {
    pub name: String,
    pub ty: ColType,
    pub primary: bool,
    pub not_null: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Int(i64),
    Str(String),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CmpOp {
    Eq,
    Lt,
    Le,
    Gt,
    Ge,
}

#[derive(Debug, Clone)]
pub enum Expr {
    Cmp { col: String, op: CmpOp, value: Value },
    And(Box<Expr>, Box<Expr>),
}

#[derive(Debug, Clone)]
pub enum OrderBy {
    Column { name: String, asc: bool },
    L2 { col: String, target: String, asc: bool },
}

#[derive(Debug, Clone)]
pub enum SelectItem {
    Star,
    Col(String),
    L2 { col: String, target: String, alias: String },
}

#[derive(Debug, Clone)]
pub enum Stmt {
    CreateTable {
        name: String,
        cols: Vec<ColumnDef>,
    },
    CreateVectorIndex {
        #[allow(dead_code)]
        table: String,
        #[allow(dead_code)]
        column: String,
    },
    Insert {
        table: String,
        columns: Vec<String>,
        rows: Vec<Vec<Value>>,
    },
    Select {
        table: String,
        items: Vec<SelectItem>,
        filter: Option<Expr>,
        orders: Vec<OrderBy>,
        limit: Option<usize>,
    },
    Update {
        table: String,
        sets: Vec<(String, Value)>,
        filter: Option<Expr>,
    },
    Delete {
        table: String,
        filter: Option<Expr>,
    },
    Begin,
    Commit,
    Rollback,
}

#[derive(Debug, Clone, PartialEq)]
enum Tok {
    Ident(String),
    Int(i64),
    Str(String),
    Punc(char),
    Op(String),
    Eof,
}

struct Lexer<'a> {
    src: &'a [u8],
    pos: usize,
}

impl<'a> Lexer<'a> {
    fn new(s: &'a str) -> Self {
        Lexer { src: s.as_bytes(), pos: 0 }
    }
    fn tokens(mut self) -> Result<Vec<Tok>, String> {
        let mut out = Vec::new();
        loop {
            let t = self.next_tok()?;
            let end = t == Tok::Eof;
            out.push(t);
            if end {
                break;
            }
        }
        Ok(out)
    }
    fn peek(&self) -> Option<u8> {
        self.src.get(self.pos).copied()
    }
    fn next_tok(&mut self) -> Result<Tok, String> {
        while let Some(c) = self.peek() {
            if c.is_ascii_whitespace() {
                self.pos += 1;
            } else {
                break;
            }
        }
        let c = match self.peek() {
            Some(c) => c,
            None => return Ok(Tok::Eof),
        };
        if c.is_ascii_alphabetic() || c == b'_' {
            let start = self.pos;
            while let Some(c) = self.peek() {
                if c.is_ascii_alphanumeric() || c == b'_' {
                    self.pos += 1;
                } else {
                    break;
                }
            }
            let s = std::str::from_utf8(&self.src[start..self.pos]).unwrap().to_string();
            return Ok(Tok::Ident(s));
        }
        if c.is_ascii_digit() {
            let start = self.pos;
            while let Some(c) = self.peek() {
                if c.is_ascii_digit() {
                    self.pos += 1;
                } else {
                    break;
                }
            }
            let s = std::str::from_utf8(&self.src[start..self.pos]).unwrap();
            let v: i64 = s.parse().map_err(|_| "integer out of range".to_string())?;
            return Ok(Tok::Int(v));
        }
        if c == b'\'' {
            self.pos += 1;
            let mut s = String::new();
            loop {
                let ch = self.peek().ok_or("unterminated string")?;
                self.pos += 1;
                if ch == b'\'' {
                    if self.peek() == Some(b'\'') {
                        s.push('\'');
                        self.pos += 1;
                        continue;
                    }
                    break;
                }
                let start = self.pos - 1;
                let len = if ch < 0x80 { 1 } else if ch >> 5 == 0b110 { 2 } else if ch >> 4 == 0b1110 { 3 } else { 4 };
                let end = start + len;
                if end > self.src.len() {
                    return Err("truncated string".into());
                }
                s.push_str(std::str::from_utf8(&self.src[start..end]).map_err(|e| e.to_string())?);
                self.pos = end;
            }
            return Ok(Tok::Str(s));
        }
        self.pos += 1;
        match c {
            b'(' | b')' | b',' | b';' | b'*' => Ok(Tok::Punc(c as char)),
            b'=' => Ok(Tok::Op("=".into())),
            b'<' => {
                if self.peek() == Some(b'=') {
                    self.pos += 1;
                    Ok(Tok::Op("<=".into()))
                } else {
                    Ok(Tok::Op("<".into()))
                }
            }
            b'>' => {
                if self.peek() == Some(b'=') {
                    self.pos += 1;
                    Ok(Tok::Op(">=".into()))
                } else {
                    Ok(Tok::Op(">".into()))
                }
            }
            b'-' => {
                // negative number literal
                if let Some(d) = self.peek() {
                    if d.is_ascii_digit() {
                        let start = self.pos;
                        while let Some(c) = self.peek() {
                            if c.is_ascii_digit() {
                                self.pos += 1;
                            } else {
                                break;
                            }
                        }
                        let s = std::str::from_utf8(&self.src[start..self.pos]).unwrap();
                        let v: i64 = format!("-{}", s).parse().map_err(|_| "integer out of range".to_string())?;
                        return Ok(Tok::Int(v));
                    }
                }
                Err("unexpected '-'".into())
            }
            _ => Err(format!("unexpected character '{}'", c as char)),
        }
    }
}

pub struct Parser {
    toks: Vec<Tok>,
    pos: usize,
}

impl Parser {
    pub fn new(sql: &str) -> Result<Self, String> {
        let toks = Lexer::new(sql).tokens()?;
        Ok(Parser { toks, pos: 0 })
    }
    fn peek(&self) -> &Tok {
        self.toks.get(self.pos).unwrap_or(&Tok::Eof)
    }
    fn next(&mut self) -> Tok {
        let t = self.toks.get(self.pos).cloned().unwrap_or(Tok::Eof);
        self.pos += 1;
        t
    }
    fn eat_kw(&mut self, kw: &str) -> bool {
        if let Tok::Ident(s) = self.peek() {
            if s.eq_ignore_ascii_case(kw) {
                self.pos += 1;
                return true;
            }
        }
        false
    }
    fn expect_kw(&mut self, kw: &str) -> Result<(), String> {
        if self.eat_kw(kw) {
            Ok(())
        } else {
            Err(format!("expected {}", kw))
        }
    }
    fn eat_punc(&mut self, c: char) -> bool {
        if self.peek() == &Tok::Punc(c) {
            self.pos += 1;
            true
        } else {
            false
        }
    }
    fn expect_punc(&mut self, c: char) -> Result<(), String> {
        if self.eat_punc(c) {
            Ok(())
        } else {
            Err(format!("expected '{}'", c))
        }
    }
    fn ident(&mut self) -> Result<String, String> {
        match self.next() {
            Tok::Ident(s) => Ok(s),
            t => Err(format!("expected identifier, got {:?}", t)),
        }
    }

    pub fn parse_stmt(&mut self) -> Result<Stmt, String> {
        let stmt = self.stmt()?;
        self.eat_punc(';');
        Ok(stmt)
    }

    fn stmt(&mut self) -> Result<Stmt, String> {
        if self.eat_kw("CREATE") {
            if self.eat_kw("TABLE") {
                return self.create_table();
            }
            if self.eat_kw("VECTOR") {
                self.expect_kw("INDEX")?;
                let _name = self.ident()?;
                self.expect_kw("ON")?;
                let table = self.ident()?;
                self.expect_punc('(')?;
                let column = self.ident()?;
                self.expect_punc(')')?;
                return Ok(Stmt::CreateVectorIndex { table, column });
            }
            return Err("unsupported_sql".into());
        }
        if self.eat_kw("INSERT") {
            self.expect_kw("INTO")?;
            let table = self.ident()?;
            let mut col_list: Vec<String> = Vec::new();
            if self.eat_punc('(') {
                loop {
                    col_list.push(self.ident()?);
                    if !self.eat_punc(',') {
                        break;
                    }
                }
                self.expect_punc(')')?;
            }
            self.expect_kw("VALUES")?;
            let mut rows = Vec::new();
            loop {
                self.expect_punc('(')?;
                let mut row = Vec::new();
                loop {
                    row.push(self.value()?);
                    if !self.eat_punc(',') {
                        break;
                    }
                }
                self.expect_punc(')')?;
                rows.push(row);
                if !self.eat_punc(',') {
                    break;
                }
            }
            return Ok(Stmt::Insert { table, columns: col_list, rows });
        }
        if self.eat_kw("SELECT") {
            return self.select();
        }
        if self.eat_kw("UPDATE") {
            let table = self.ident()?;
            self.expect_kw("SET")?;
            let mut sets = Vec::new();
            loop {
                let col = self.ident()?;
                match self.next() {
                    Tok::Op(op) if op == "=" => {}
                    _ => return Err("expected '='".into()),
                }
                let v = self.value()?;
                sets.push((col, v));
                if !self.eat_punc(',') {
                    break;
                }
            }
            let filter = self.opt_where()?;
            return Ok(Stmt::Update { table, sets, filter });
        }
        if self.eat_kw("DELETE") {
            self.expect_kw("FROM")?;
            let table = self.ident()?;
            let filter = self.opt_where()?;
            return Ok(Stmt::Delete { table, filter });
        }
        if self.eat_kw("BEGIN") {
            return Ok(Stmt::Begin);
        }
        if self.eat_kw("COMMIT") {
            return Ok(Stmt::Commit);
        }
        if self.eat_kw("ROLLBACK") {
            return Ok(Stmt::Rollback);
        }
        Err("unsupported_sql".into())
    }

    fn create_table(&mut self) -> Result<Stmt, String> {
        let name = self.ident()?;
        self.expect_punc('(')?;
        let mut cols = Vec::new();
        loop {
            let col_name = self.ident()?;
            let ty_name = self.ident()?;
            let ty = match ty_name.to_ascii_uppercase().as_str() {
                "BIGINT" => ColType::Bigint,
                "VARCHAR" => {
                    self.expect_punc('(')?;
                    let n = match self.next() {
                        Tok::Int(n) if n > 0 => n as usize,
                        _ => return Err("bad varchar length".into()),
                    };
                    self.expect_punc(')')?;
                    ColType::Varchar(n)
                }
                "VECTOR" => {
                    self.expect_punc('(')?;
                    let n = match self.next() {
                        Tok::Int(n) if n > 0 => n as usize,
                        _ => return Err("bad vector dimension".into()),
                    };
                    self.expect_punc(')')?;
                    ColType::Vector(n)
                }
                other => return Err(format!("unsupported type {}", other)),
            };
            // NOT NULL 修饰：记录并执行
            let mut not_null = false;
            if self.eat_kw("NOT") {
                self.expect_kw("NULL")?;
                not_null = true;
            } else if self.eat_kw("NULL") {
                not_null = false;
            }
            let mut primary = false;
            if self.eat_kw("PRIMARY") {
                self.expect_kw("KEY")?;
                primary = true;
            }
            cols.push(ColumnDef { name: col_name, ty, primary, not_null });
            if !self.eat_punc(',') {
                break;
            }
            // 表级约束：PRIMARY KEY (col)
            if self.eat_kw("PRIMARY") {
                self.expect_kw("KEY")?;
                self.expect_punc('(')?;
                let pk_name = self.ident()?;
                self.expect_punc(')')?;
                if let Some(c) = cols.iter_mut().find(|c| c.name.eq_ignore_ascii_case(&pk_name)) {
                    c.primary = true;
                }
                if !self.eat_punc(',') {
                    break;
                }
            }
        }
        self.expect_punc(')')?;
        Ok(Stmt::CreateTable { name, cols })
    }

    fn select(&mut self) -> Result<Stmt, String> {
        let mut items = Vec::new();
        loop {
            if self.eat_punc('*') {
                items.push(SelectItem::Star);
            } else if let Tok::Ident(id) = self.peek().clone() {
                if id.eq_ignore_ascii_case("L2_DISTANCE") {
                    self.pos += 1;
                    self.expect_punc('(')?;
                    let col = self.ident()?;
                    self.expect_punc(',')?;
                    let target = match self.next() {
                        Tok::Str(s) => s,
                        _ => return Err("expected vector literal".into()),
                    };
                    self.expect_punc(')')?;
                    let mut alias = "distance".to_string();
                    if self.eat_kw("AS") {
                        alias = self.ident()?;
                    }
                    items.push(SelectItem::L2 { col, target, alias });
                } else {
                    self.pos += 1;
                    items.push(SelectItem::Col(id));
                }
            } else {
                return Err("bad select list".into());
            }
            if !self.eat_punc(',') {
                break;
            }
        }
        self.expect_kw("FROM")?;
        let table = self.ident()?;
        let filter = self.opt_where()?;
        let mut orders: Vec<OrderBy> = Vec::new();
        if self.eat_kw("ORDER") {
            self.expect_kw("BY")?;
            loop {
                let first = self.ident()?;
                let parsed = if first.eq_ignore_ascii_case("L2_DISTANCE") {
                    self.expect_punc('(')?;
                    let vcol = self.ident()?;
                    self.expect_punc(',')?;
                    let target = match self.next() {
                        Tok::Str(s) => s,
                        _ => return Err("expected vector literal".into()),
                    };
                    self.expect_punc(')')?;
                    OrderBy::L2 { col: vcol, target, asc: true }
                } else {
                    OrderBy::Column { name: first, asc: true }
                };
                let asc = if self.eat_kw("DESC") {
                    false
                } else {
                    self.eat_kw("ASC");
                    true
                };
                orders.push(match parsed {
                    OrderBy::Column { name, .. } => OrderBy::Column { name, asc },
                    OrderBy::L2 { col, target, .. } => OrderBy::L2 { col, target, asc },
                });
                if !self.eat_punc(',') {
                    break;
                }
            }
        }
        let mut limit = None;
        if self.eat_kw("LIMIT") {
            match self.next() {
                Tok::Int(n) if n >= 0 => limit = Some(n as usize),
                _ => return Err("bad limit".into()),
            }
        }
        Ok(Stmt::Select { table, items, filter, orders, limit })
    }

    fn opt_where(&mut self) -> Result<Option<Expr>, String> {
        if !self.eat_kw("WHERE") {
            return Ok(None);
        }
        let mut expr = self.condition()?;
        while self.eat_kw("AND") {
            let rhs = self.condition()?;
            expr = Expr::And(Box::new(expr), Box::new(rhs));
        }
        Ok(Some(expr))
    }

    fn condition(&mut self) -> Result<Expr, String> {
        let col = self.ident()?;
        let op = match self.next() {
            Tok::Op(op) => match op.as_str() {
                "=" => CmpOp::Eq,
                "<" => CmpOp::Lt,
                "<=" => CmpOp::Le,
                ">" => CmpOp::Gt,
                ">=" => CmpOp::Ge,
                _ => return Err("bad operator".into()),
            },
            _ => return Err("expected comparison operator".into()),
        };
        let value = self.value()?;
        Ok(Expr::Cmp { col, op, value })
    }

    fn value(&mut self) -> Result<Value, String> {
        match self.next() {
            Tok::Int(i) => Ok(Value::Int(i)),
            Tok::Str(s) => Ok(Value::Str(s)),
            Tok::Ident(s) if s.eq_ignore_ascii_case("NULL") => Ok(Value::Null),
            t => Err(format!("bad value {:?}", t)),
        }
    }
}
