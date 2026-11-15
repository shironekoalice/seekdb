//! Storage, WAL, transactions and execution for MiniVectorDB.

use crate::json;
use crate::sql::{ColType, CmpOp, Expr, SelectItem, Stmt, Value};
use std::collections::{BTreeSet, HashMap};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};

pub const PAGE: usize = 8192;

#[derive(Debug)]
pub struct DbError {
    pub code: String,
    pub message: String,
}

impl DbError {
    pub fn new(code: &str, message: &str) -> Self {
        DbError { code: code.into(), message: message.into() }
    }
}

type Result<T> = std::result::Result<T, DbError>;


// ---------- 有界页缓存（内存上限用例必需） ----------

pub struct PageStore {
    file: File,
    arena: Vec<Option<PageEntry>>,
    index: HashMap<u32, usize>,
    free: Vec<usize>,
    cap: usize,
    tick: u64,
}

struct PageEntry {
    id: u32,
    data: Box<[u8]>,
    dirty: bool,
    tick: u64,
}

impl PageStore {
    fn new(file: File, cap: usize) -> Self {
        PageStore { file, arena: Vec::new(), index: HashMap::new(), free: Vec::new(), cap: cap.max(16), tick: 0 }
    }

    fn read_from_file(&mut self, id: u32) -> Box<[u8]> {
        let mut buf = vec![0u8; PAGE].into_boxed_slice();
        let off = id as u64 * PAGE as u64;
        let _ = self.file.seek(SeekFrom::Start(off));
        let _ = self.file.read_exact(&mut buf);
        buf
    }

    fn evict_one(&mut self) -> bool {
        // 只淘汰干净页：脏页属于未提交事务，必须留在内存里
        let mut victim: Option<usize> = None;
        let mut oldest = u64::MAX;
        for (i, e) in self.arena.iter().enumerate() {
            if let Some(entry) = e {
                if !entry.dirty && entry.tick < oldest {
                    oldest = entry.tick;
                    victim = Some(i);
                }
            }
        }
        match victim {
            Some(i) => {
                if let Some(entry) = self.arena[i].take() {
                    self.index.remove(&entry.id);
                    self.free.push(i);
                }
                true
            }
            None => false,
        }
    }

    fn ensure(&mut self, id: u32) -> usize {
        if let Some(&slot) = self.index.get(&id) {
            let tick = self.tick;
            self.tick += 1;
            if let Some(e) = self.arena[slot].as_mut() {
                e.tick = tick;
            }
            return slot;
        }
        while self.index.len() >= self.cap {
            if !self.evict_one() {
                break;
            }
        }
        let data = self.read_from_file(id);
        let tick = self.tick;
        self.tick += 1;
        let entry = PageEntry { id, data, dirty: false, tick };
        let slot = match self.free.pop() {
            Some(i) => {
                self.arena[i] = Some(entry);
                i
            }
            None => {
                self.arena.push(Some(entry));
                self.arena.len() - 1
            }
        };
        self.index.insert(id, slot);
        slot
    }

    pub fn page(&mut self, id: u32) -> &[u8] {
        let slot = self.ensure(id);
        &self.arena[slot].as_ref().unwrap().data
    }

    pub fn page_mut(&mut self, id: u32) -> &mut [u8] {
        let slot = self.ensure(id);
        let e = self.arena[slot].as_mut().unwrap();
        e.dirty = true;
        &mut e.data
    }

    fn mark_dirty(&mut self, id: u32) {
        let slot = self.ensure(id);
        if let Some(e) = self.arena[slot].as_mut() {
            e.dirty = true;
        }
    }

    fn dirty_ids(&self) -> Vec<u32> {
        self.arena
            .iter()
            .flatten()
            .filter(|e| e.dirty)
            .map(|e| e.id)
            .collect()
    }

    fn snapshot_page(&mut self, id: u32) -> Vec<u8> {
        self.page(id).to_vec()
    }

    fn restore_page(&mut self, id: u32, data: &[u8]) {
        let slot = self.ensure(id);
        if let Some(e) = self.arena[slot].as_mut() {
            e.data.copy_from_slice(data);
            e.dirty = true;
        }
    }

    fn clear_dirty(&mut self, ids: &[u32]) {
        for id in ids {
            if let Some(&slot) = self.index.get(id) {
                if let Some(e) = self.arena[slot].as_mut() {
                    e.dirty = false;
                }
            }
        }
    }

    fn flush_all(&mut self) -> std::io::Result<()> {
        let ids: Vec<u32> = self.arena.iter().flatten().map(|e| e.id).collect();
        for id in ids {
            self.write_page(id)?;
        }
        Ok(())
    }

    fn write_page(&mut self, id: u32) -> std::io::Result<()> {
        let slot = self.ensure(id);
        let e = self.arena[slot].as_ref().unwrap();
        let off = id as u64 * PAGE as u64;
        self.file.seek(SeekFrom::Start(off))?;
        self.file.write_all(&e.data)?;
        if let Some(e) = self.arena[slot].as_mut() {
            e.dirty = false;
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct Column {
    pub name: String,
    pub ty: ColType,
}

#[derive(Clone, Debug)]
pub struct Table {
    pub name: String,
    pub cols: Vec<Column>,
    pub pk: usize,
    pub root: u32,
    pub last: u32,
    pub vec_index_col: Option<usize>,
}

/// IVF-Flat 向量索引：质心 + 每个簇的成员主键
#[derive(Clone, Debug, Default)]
pub struct VecIndex {
    pub dim: usize,
    pub centroids: Vec<Vec<f32>>,
    /// 每个簇的包围球半径（成员到质心的最大距离），用于安全剪枝
    pub radius: Vec<f32>,
    pub members: Vec<Vec<i64>>,
}

impl VecIndex {
    fn nearest(&self, v: &[f32]) -> usize {
        let mut best = 0;
        let mut best_d = f32::INFINITY;
        for (i, c) in self.centroids.iter().enumerate() {
            let d = l2(v, c);
            if d < best_d {
                best_d = d;
                best = i;
            }
        }
        best
    }
    fn refresh_radius(&mut self, ci: usize) {
        let _ = ci;
    }

    fn remove(&mut self, pk: i64) {
        for bucket in self.members.iter_mut() {
            if let Some(pos) = bucket.iter().position(|x| *x == pk) {
                bucket.swap_remove(pos);
                return;
            }
        }
    }
    fn add(&mut self, pk: i64, v: &[f32]) {
        if self.centroids.is_empty() {
            return;
        }
        let c = self.nearest(v);
        if !self.members[c].contains(&pk) {
            self.members[c].push(pk);
        }
        let d = l2(v, &self.centroids[c]);
        if d > self.radius[c] {
            self.radius[c] = d;
        }
    }
}

#[derive(Clone, Debug)]
pub struct Row {
    pub values: Vec<Cell>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Cell {
    Null,
    Int(i64),
    Str(String),
    Vec(Vec<f32>),
}

impl Cell {
    pub fn to_json(&self) -> json::Json {
        match self {
            Cell::Null => json::Json::Null,
            Cell::Int(i) => json::Json::Int(*i),
            Cell::Str(s) => json::Json::Str(s.clone()),
            Cell::Vec(v) => json::Json::Arr(v.iter().map(|f| json::Json::Float(*f as f64)).collect()),
        }
    }
}

pub struct Db {
    pub tables: Vec<Table>,
    pub pages: PageStore,
    pub next_page: u32,
    pub search_mode: String,
    pk_maps: Vec<HashMap<i64, (u32, u16)>>,
    vec_idx: Vec<Option<VecIndex>>,
    undo: Vec<(u32, Vec<u8>)>,
    undone: BTreeSet<u32>,
    in_txn: bool,
    db_path: String,
    wal_path: String,
}

fn read_u16(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}
fn write_u16(b: &mut [u8], off: usize, v: u16) {
    b[off..off + 2].copy_from_slice(&v.to_le_bytes());
}
fn read_u32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}
fn write_u32(b: &mut [u8], off: usize, v: u32) {
    b[off..off + 4].copy_from_slice(&v.to_le_bytes());
}

impl Db {
    pub fn open(dir: &str) -> Result<Db> {
        std::fs::create_dir_all(dir).ok();
        let db_path = format!("{}/minivector.db", dir);
        let wal_path = format!("{}/wal.log", dir);
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&db_path)
            .map_err(|e| DbError::new("io_error", &e.to_string()))?;
        let cap_mb: usize = std::env::var("MVDB_CACHE_MB").ok().and_then(|v| v.parse().ok()).unwrap_or(64);
        let cap = (cap_mb * 1024 * 1024 / PAGE).max(64);
        let mut db = Db {
            tables: Vec::new(),
            pages: PageStore::new(file, cap),
            next_page: 1,
            search_mode: "exact".to_string(),
            pk_maps: Vec::new(),
            vec_idx: Vec::new(),
            undo: Vec::new(),
            undone: BTreeSet::new(),
            in_txn: false,
            db_path,
            wal_path,
        };
        db.load_catalog_page();
        db.recover()?;
        db.load_catalog_page();
        db.rebuild_indexes();
        Ok(db)
    }

    fn load_catalog_page(&mut self) {
        let (len, text) = {
            let meta = self.pages.page(0);
            (read_u32(meta, 4) as usize, if read_u32(meta, 4) as usize > 0 && 8 + (read_u32(meta, 4) as usize) <= PAGE {
                std::str::from_utf8(&meta[8..8 + read_u32(meta, 4) as usize]).ok().map(|s| s.to_string())
            } else { None })
        };
        let _ = len;
        if let Some(t) = text {
            self.load_catalog(&t);
        }
    }

    fn load_catalog(&mut self, text: &str) {
        if let Ok(j) = json::parse(text) {
            if let Some(n) = j.get("next_page").and_then(|v| v.as_i64()) {
                if n as u32 > self.next_page {
                    self.next_page = n as u32;
                }
            }
            if let Some(json::Json::Arr(items)) = j.get("tables") {
                let mut tables = Vec::new();
                for t in items {
                    let name = t.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let root = t.get("root").and_then(|v| v.as_i64()).unwrap_or(1) as u32;
                    let last = t.get("last").and_then(|v| v.as_i64()).unwrap_or(root as i64) as u32;
                    let pk = t.get("pk").and_then(|v| v.as_i64()).unwrap_or(0) as usize;
                    let vic = t.get("vic").and_then(|v| v.as_i64()).map(|v| v as usize);
                    let mut cols = Vec::new();
                    if let Some(json::Json::Arr(cs)) = t.get("cols") {
                        for c in cs {
                            let cname = c.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                            let ty = match c.get("ty").and_then(|v| v.as_str()).unwrap_or("") {
                                "bigint" => ColType::Bigint,
                                "varchar" => ColType::Varchar(c.get("n").and_then(|v| v.as_i64()).unwrap_or(255) as usize),
                                "vector" => ColType::Vector(c.get("n").and_then(|v| v.as_i64()).unwrap_or(1) as usize),
                                _ => ColType::Bigint,
                            };
                            cols.push(Column { name: cname, ty });
                        }
                    }
                    tables.push(Table { name, cols, pk, root, last, vec_index_col: vic });
                }
                self.tables = tables;
            }
        }
    }

    fn catalog_json(&self) -> String {
        let mut tables = Vec::new();
        for t in &self.tables {
            let mut cols = Vec::new();
            for c in &t.cols {
                let (ty, n) = match c.ty {
                    ColType::Bigint => ("bigint".to_string(), 0),
                    ColType::Varchar(n) => ("varchar".to_string(), n),
                    ColType::Vector(n) => ("vector".to_string(), n),
                };
                let mut m = vec![
                    ("name".to_string(), json::Json::Str(c.name.clone())),
                    ("ty".to_string(), json::Json::Str(ty)),
                ];
                if n > 0 {
                    m.push(("n".to_string(), json::Json::Int(n as i64)));
                }
                cols.push(json::Json::Obj(m));
            }
            tables.push(json::Json::Obj(vec![
                ("name".to_string(), json::Json::Str(t.name.clone())),
                ("pk".to_string(), json::Json::Int(t.pk as i64)),
                ("root".to_string(), json::Json::Int(t.root as i64)),
                ("last".to_string(), json::Json::Int(t.last as i64)),
                ("cols".to_string(), json::Json::Arr(cols)),
                ("vic".to_string(), match t.vec_index_col {
                    Some(i) => json::Json::Int(i as i64),
                    None => json::Json::Null,
                }),
            ]));
        }
        json::to_string(&json::Json::Obj(vec![
            ("tables".to_string(), json::Json::Arr(tables)),
            ("next_page".to_string(), json::Json::Int(self.next_page as i64)),
        ]))
    }

    fn flush_catalog_into_page(&mut self) {
        let text = self.catalog_json();
        let bytes = text.as_bytes();
        let mut page = vec![0u8; PAGE];
        page[0..4].copy_from_slice(b"MVDB");
        let len = bytes.len().min(PAGE - 8);
        write_u32(&mut page, 4, len as u32);
        page[8..8 + len].copy_from_slice(&bytes[..len]);
        {
            let dst = self.pages.page_mut(0);
            dst.copy_from_slice(&page);
        }
    }

    // ---------- pages ----------

    fn new_page(&mut self) -> u32 {
        let id = self.next_page;
        self.next_page += 1;
        {
            let page = self.pages.page_mut(id);
            for b in page.iter_mut() {
                *b = 0;
            }
            write_u16(page, 0, 0);
            write_u16(page, 2, 8);
            write_u32(page, 4, 0);
        }
        self.record_undo(id);
        self.flush_catalog_into_page();
        id
    }

    fn page_slot_count(page: &[u8]) -> usize {
        read_u16(page, 0) as usize
    }
    fn page_free_start(page: &[u8]) -> usize {
        read_u16(page, 2) as usize
    }
    fn slot_off(page: &[u8], i: usize) -> usize {
        read_u16(page, PAGE - 2 * (i + 1)) as usize
    }
    fn set_slot_off(page: &mut [u8], i: usize, off: usize) {
        write_u16(page, PAGE - 2 * (i + 1), off as u16);
    }

    /// 首次修改某页时记录前镜像（用于回滚）
    fn record_undo(&mut self, id: u32) {
        if self.undone.contains(&id) {
            return;
        }
        let before = self.pages.snapshot_page(id);
        self.undo.push((id, before));
        self.undone.insert(id);
    }

    fn insert_row_into_page(&mut self, page_id: u32, row: &[u8]) -> Option<usize> {
        self.record_undo(page_id);
        let page = self.pages.page_mut(page_id);
        let slots = Db::page_slot_count(page);
        let free = Db::page_free_start(page);
        let need = row.len() + 2;
        if free + need > PAGE - 2 * (slots + 1) {
            return None;
        }
        page[free..free + row.len()].copy_from_slice(row);
        Db::set_slot_off(page, slots, free);
        write_u16(page, 2, (free + row.len()) as u16);
        write_u16(page, 0, (slots + 1) as u16);
        Some(slots)
    }

    fn read_row(&mut self, page_id: u32, slot: usize) -> Option<RowRef> {
        let page = self.pages.page(page_id);
        let slots = Db::page_slot_count(page);
        if slot >= slots {
            return None;
        }
        let off = Db::slot_off(page, slot);
        if off == 0 {
            return None;
        }
        Some(RowRef { page: page_id, off })
    }

    // ---------- WAL ----------

    fn append_wal_commit(&mut self) -> Result<()> {
        let dirty = self.pages.dirty_ids();
        if dirty.is_empty() {
            return Ok(());
        }
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.wal_path)
            .map_err(|e| DbError::new("io_error", &e.to_string()))?;
        for id in &dirty {
            let mut head = [0u8; 8];
            head[0..4].copy_from_slice(&(PAGE as u32).to_le_bytes());
            head[4..8].copy_from_slice(&id.to_le_bytes());
            f.write_all(&head).map_err(|e| DbError::new("io_error", &e.to_string()))?;
            f.write_all(&self.pages.page(*id)).map_err(|e| DbError::new("io_error", &e.to_string()))?;
        }
        let mut marker = [0u8; 8];
        marker[0..4].copy_from_slice(&0u32.to_le_bytes());
        marker[4..8].copy_from_slice(&(dirty.len() as u32).to_le_bytes());
        f.write_all(&marker).map_err(|e| DbError::new("io_error", &e.to_string()))?;
        f.sync_data().map_err(|e| DbError::new("io_error", &e.to_string()))?;
        for id in &dirty {
            self.pages
                .write_page(*id)
                .map_err(|e| DbError::new("io_error", &e.to_string()))?;
        }
        Ok(())
    }

    fn recover(&mut self) -> Result<()> {
        let mut data = Vec::new();
        match File::open(&self.wal_path) {
            Ok(mut f) => {
                f.read_to_end(&mut data).map_err(|e| DbError::new("io_error", &e.to_string()))?;
            }
            Err(_) => return Ok(()),
        }
        let mut off = 0usize;
        let mut batch: Vec<(u32, Vec<u8>)> = Vec::new();
        while off + 8 <= data.len() {
            let len = read_u32(&data, off) as usize;
            let pid = read_u32(&data, off + 4);
            if len == 0 {
                for (page_id, page) in batch.drain(..) {
                    self.pages.restore_page(page_id, &page);
                    if page_id >= self.next_page {
                        self.next_page = page_id + 1;
                    }
                }
                off += 8;
                continue;
            }
            if len != PAGE || off + 8 + PAGE > data.len() {
                break;
            }
            batch.push((pid, data[off + 8..off + 8 + PAGE].to_vec()));
            off += 8 + PAGE;
        }
        Ok(())
    }

    // ---------- transactions ----------

    pub fn begin(&mut self) -> Result<()> {
        if self.in_txn {
            return Err(DbError::new("nested_transaction", "transaction already open"));
        }
        self.in_txn = true;
        self.undo.clear();
        self.undone.clear();
        Ok(())
    }

    pub fn commit(&mut self) -> Result<()> {
        self.append_wal_commit()?;
        self.in_txn = false;
        self.undo.clear();
        self.undone.clear();
        Ok(())
    }

    pub fn rollback(&mut self) -> Result<()> {
        while let Some((id, before)) = self.undo.pop() {
            self.pages.restore_page(id, &before);
        }
        self.undone.clear();
        self.in_txn = false;
        self.rebuild_indexes();
        Ok(())
    }

    pub fn shutdown(&mut self) -> Result<()> {
        if self.in_txn {
            self.rollback()?;
        }
        self.append_wal_commit()?;
        self.pages
            .flush_all()
            .map_err(|e| DbError::new("io_error", &e.to_string()))?;
        let _ = File::create(&self.wal_path);
        Ok(())
    }

    // ---------- indexes (M4/M5) ----------

    fn rebuild_indexes(&mut self) {
        self.pk_maps = vec![HashMap::new(); self.tables.len()];
        self.vec_idx = vec![None; self.tables.len()];
        for ti in 0..self.tables.len() {
            let t = self.tables[ti].clone();
            for (page, slot, row) in self.scan(&t) {
                if let Cell::Int(pk) = row.values[t.pk] {
                    self.pk_maps[ti].insert(pk, (page, slot as u16));
                }
            }
            if let Some(col) = t.vec_index_col {
                self.build_vec_index(ti, col);
            }
        }
    }

    fn build_vec_index(&mut self, ti: usize, col: usize) {
        let t = self.tables[ti].clone();
        let dim = match t.cols[col].ty {
            ColType::Vector(d) => d,
            _ => return,
        };
        let mut points: Vec<(i64, Vec<f32>)> = Vec::new();
        for (_, _, row) in self.scan(&t) {
            if let (Cell::Int(pk), Cell::Vec(v)) = (&row.values[t.pk], &row.values[col]) {
                points.push((*pk, v.clone()));
            }
        }
        self.vec_idx[ti] = Some(build_ivf(points, dim));
    }

    /// 簇级下界剪枝的 TopK（不牺牲召回）：返回 (距离, 主键, 位置) 升序
    fn ann_topk(&mut self, ti: usize, target: &[f32], k: usize) -> Vec<(f32, i64, (u32, u16))> {
        let (centroids, radius, members, vcol) = match (&self.vec_idx[ti], self.tables[ti].vec_index_col) {
            (Some(idx), Some(c)) if !idx.centroids.is_empty() && k > 0 => (
                idx.centroids.clone(),
                idx.radius.clone(),
                idx.members.clone(),
                c,
            ),
            _ => return Vec::new(),
        };
        let mut order: Vec<(usize, f32)> = centroids
            .iter()
            .enumerate()
            .map(|(i, c)| (i, l2(target, c)))
            .collect();
        order.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));

        let table = self.tables[ti].clone();
        let mut heap: Vec<(f32, i64, (u32, u16))> = Vec::with_capacity(k + 1);
        let mut kth = f32::INFINITY;
        let cmp = |a: &(f32, i64, (u32, u16)), b: &(f32, i64, (u32, u16))| {
            a.0.partial_cmp(&b.0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.1.cmp(&b.1))
        };
        for (ci, cd) in order {
            let r = radius.get(ci).copied().unwrap_or(0.0);
            let bound = cd - r;
            let guard = kth * 1e-5_f32 + 1e-6;
            if heap.len() >= k && bound > kth + guard {
                break;
            }
            for pk in members[ci].iter() {
                let loc = match self.pk_maps[ti].get(pk) {
                    Some(l) => *l,
                    None => continue,
                };
                let d = match self.dist_at(&table, loc.0, loc.1 as usize, vcol, target) {
                    Some(d) => d,
                    None => continue,
                };
                if heap.len() < k {
                    heap.push((d, *pk, loc));
                    heap.sort_by(cmp);
                    if heap.len() == k {
                        kth = heap[k - 1].0;
                    }
                } else if cmp(&(d, *pk, loc), &(kth, heap[k - 1].1, loc)) == std::cmp::Ordering::Less {
                    heap.pop();
                    heap.push((d, *pk, loc));
                    heap.sort_by(cmp);
                    kth = heap[k - 1].0;
                }
            }
        }
        heap
    }

    /// 零分配：直接在页字节上计算到查询向量的 L2 距离
    fn dist_at(&mut self, t: &Table, page_id: u32, slot: usize, vcol: usize, q: &[f32]) -> Option<f32> {
        let off = self.read_row(page_id, slot)?.off;
        let bytes = self.pages.page(page_id);
        let bytes = &bytes[off..];
        let nulls = read_u16(bytes, 0);
        let mut p = 2usize;
        for (i, col) in t.cols.iter().enumerate() {
            if nulls & (1 << i) != 0 {
                continue;
            }
            match col.ty {
                ColType::Bigint => p += 8,
                ColType::Varchar(_) => {
                    let n = read_u32(bytes, p) as usize;
                    p += 4 + n;
                }
                ColType::Vector(_) => {
                    let dim = read_u16(bytes, p) as usize;
                    p += 2;
                    if i == vcol {
                        let mut sum = 0f32;
                        for j in 0..dim.min(q.len()) {
                            let f = f32::from_le_bytes(bytes[p + j * 4..p + j * 4 + 4].try_into().ok()?);
                            let d = f - q[j];
                            sum += d * d;
                        }
                        return Some(sum.sqrt());
                    }
                    p += dim * 4;
                }
            }
        }
        None
    }

    fn read_rows_at(&mut self, t: &Table, locs: &[(u32, u16)]) -> Vec<(u32, usize, Row)> {
        let mut out = Vec::with_capacity(locs.len());
        for (page_id, slot) in locs {
            let off = match self.read_row(*page_id, *slot as usize) {
                Some(r) => r.off,
                None => continue,
            };
            let bytes = {
                let page = self.pages.page(*page_id);
                page[off..].to_vec()
            };
            let row = self.decode_row(t, &bytes);
            out.push((*page_id, *slot as usize, row));
        }
        out
    }

    // ---------- schema helpers ----------

    pub fn table(&self, name: &str) -> Result<&Table> {
        self.tables
            .iter()
            .find(|t| t.name.eq_ignore_ascii_case(name))
            .ok_or_else(|| DbError::new("table_not_found", "no such table"))
    }

    fn table_idx(&self, name: &str) -> Result<usize> {
        self.tables
            .iter()
            .position(|t| t.name.eq_ignore_ascii_case(name))
            .ok_or_else(|| DbError::new("table_not_found", "no such table"))
    }

    // ---------- row encode/decode ----------

    fn encode_row(&self, t: &Table, values: &[Cell]) -> Result<Vec<u8>> {
        let mut out = Vec::with_capacity(64);
        let mut nulls: u16 = 0;
        for (i, v) in values.iter().enumerate() {
            if *v == Cell::Null {
                nulls |= 1 << i;
            }
        }
        out.extend_from_slice(&nulls.to_le_bytes());
        for (i, col) in t.cols.iter().enumerate() {
            let v = values.get(i).unwrap_or(&Cell::Null);
            match (&col.ty, v) {
                (_, Cell::Null) => {}
                (ColType::Bigint, Cell::Int(x)) => out.extend_from_slice(&x.to_le_bytes()),
                (ColType::Varchar(_), Cell::Str(s)) => {
                    let b = s.as_bytes();
                    out.extend_from_slice(&(b.len() as u32).to_le_bytes());
                    out.extend_from_slice(b);
                }
                (ColType::Vector(_), Cell::Vec(vs)) => {
                    out.extend_from_slice(&(vs.len() as u16).to_le_bytes());
                    for f in vs {
                        out.extend_from_slice(&f.to_le_bytes());
                    }
                }
                _ => return Err(DbError::new("type_mismatch", "column type mismatch")),
            }
        }
        Ok(out)
    }

    fn decode_row(&self, t: &Table, bytes: &[u8]) -> Row {
        let nulls = read_u16(bytes, 0);
        let mut off = 2usize;
        let mut values = Vec::with_capacity(t.cols.len());
        for (i, col) in t.cols.iter().enumerate() {
            if nulls & (1 << i) != 0 {
                values.push(Cell::Null);
                continue;
            }
            match col.ty {
                ColType::Bigint => {
                    let x = i64::from_le_bytes(bytes[off..off + 8].try_into().unwrap());
                    off += 8;
                    values.push(Cell::Int(x));
                }
                ColType::Varchar(_) => {
                    let n = read_u32(bytes, off) as usize;
                    off += 4;
                    let s = String::from_utf8_lossy(&bytes[off..off + n]).to_string();
                    off += n;
                    values.push(Cell::Str(s));
                }
                ColType::Vector(_) => {
                    let n = read_u16(bytes, off) as usize;
                    off += 2;
                    let mut v = Vec::with_capacity(n);
                    for _ in 0..n {
                        let f = f32::from_le_bytes(bytes[off..off + 4].try_into().unwrap());
                        off += 4;
                        v.push(f);
                    }
                    values.push(Cell::Vec(v));
                }
            }
        }
        Row { values }
    }

    fn coerce(&self, t: &Table, col_idx: usize, v: &Value) -> Result<Cell> {
        let col = &t.cols[col_idx];
        match (v, &col.ty) {
            (Value::Null, _) => Ok(Cell::Null),
            (Value::Int(i), ColType::Bigint) => Ok(Cell::Int(*i)),
            (Value::Str(s), ColType::Varchar(n)) => {
                if s.chars().count() > *n {
                    return Err(DbError::new("value_too_long", "varchar length exceeded"));
                }
                Ok(Cell::Str(s.clone()))
            }
            (Value::Str(s), ColType::Vector(dim)) => {
                let vs = parse_vector(s, *dim)?;
                Ok(Cell::Vec(vs))
            }
            _ => Err(DbError::new("type_mismatch", "column type mismatch")),
        }
    }

    // ---------- execution ----------

    pub fn execute(&mut self, stmt: &Stmt) -> Result<ExecResult> {
        match stmt {
            Stmt::Begin => {
                self.begin()?;
                Ok(ExecResult::Ok)
            }
            Stmt::Commit => {
                self.commit()?;
                Ok(ExecResult::Ok)
            }
            Stmt::Rollback => {
                self.rollback()?;
                Ok(ExecResult::Ok)
            }
            Stmt::CreateTable { name, cols } => {
                if self.tables.iter().any(|t| t.name.eq_ignore_ascii_case(name)) {
                    return Err(DbError::new("table_exists", "table already exists"));
                }
                let pk = cols
                    .iter()
                    .position(|c| c.primary)
                    .ok_or_else(|| DbError::new("unsupported_sql", "primary key required"))?;
                if !matches!(cols[pk].ty, ColType::Bigint) {
                    return Err(DbError::new("unsupported_sql", "primary key must be BIGINT"));
                }
                let root = self.new_page();
                let table = Table {
                    name: name.clone(),
                    cols: cols.iter().map(|c| Column { name: c.name.clone(), ty: c.ty.clone() }).collect(),
                    pk,
                    root,
                    last: root,
                    vec_index_col: None,
                };
                self.tables.push(table);
                self.pk_maps.push(HashMap::new());
                self.vec_idx.push(None);
                self.flush_catalog_into_page();
                self.auto_commit()?;
                Ok(ExecResult::Ok)
            }
            Stmt::CreateVectorIndex { table, column } => {
                let ti = self.table_idx(table)?;
                let ci = self.tables[ti]
                    .cols
                    .iter()
                    .position(|c| c.name.eq_ignore_ascii_case(column))
                    .ok_or_else(|| DbError::new("column_not_found", "no such column"))?;
                if !matches!(self.tables[ti].cols[ci].ty, ColType::Vector(_)) {
                    return Err(DbError::new("type_mismatch", "VECTOR INDEX needs a vector column"));
                }
                self.tables[ti].vec_index_col = Some(ci);
                self.flush_catalog_into_page();
                self.auto_commit()?;
                self.build_vec_index(ti, ci);
                Ok(ExecResult::Ok)
            }
            Stmt::Insert { table, rows } => self.insert(table, rows),
            Stmt::Select { table, items, filter, order, limit } => {
                self.select(table, items, filter.as_ref(), order.as_ref(), *limit)
            }
            Stmt::Update { table, sets, filter } => self.update(table, sets, filter.as_ref()),
            Stmt::Delete { table, filter } => self.delete(table, filter.as_ref()),
        }
    }

    fn auto_commit(&mut self) -> Result<()> {
        if !self.in_txn {
            self.append_wal_commit()?;
        }
        Ok(())
    }

    fn scan(&mut self, t: &Table) -> Vec<(u32, usize, Row)> {
        let mut out = Vec::new();
        let mut page_id = t.root;
        while page_id != 0 {
            let (slots, next) = {
                let page = self.pages.page(page_id);
                (Db::page_slot_count(page), read_u32(page, 4))
            };
            for slot in 0..slots {
                if let Some(r) = self.read_row(page_id, slot) {
                    let bytes = {
                        let page = self.pages.page(page_id);
                        page[r.off..].to_vec()
                    };
                    let row = self.decode_row(t, &bytes);
                    out.push((page_id, slot, row));
                }
            }
            page_id = next;
        }
        out
    }

    fn insert(&mut self, table: &str, rows: &[Vec<Value>]) -> Result<ExecResult> {
        let ti = self.table_idx(table)?;
        let t = self.tables[ti].clone();
        // validate everything first (statement atomicity)
        let mut encoded = Vec::with_capacity(rows.len());
        for raw in rows {
            if raw.len() != t.cols.len() {
                return Err(DbError::new("column_count_mismatch", "column count mismatch"));
            }
            let mut cells = Vec::with_capacity(raw.len());
            for (i, v) in raw.iter().enumerate() {
                cells.push(self.coerce(&t, i, v)?);
            }
            if matches!(cells[t.pk], Cell::Null) {
                return Err(DbError::new("null_primary_key", "primary key cannot be null"));
            }
            encoded.push(cells);
        }
        // 用内存主键表查重（不再全表扫描）
        let mut batch_seen: BTreeSet<i64> = BTreeSet::new();
        for cells in &encoded {
            let pk = match &cells[t.pk] {
                Cell::Int(p) => *p,
                _ => return Err(DbError::new("null_primary_key", "primary key cannot be null")),
            };
            if self.pk_maps[ti].contains_key(&pk) || batch_seen.contains(&pk) {
                return Err(DbError::new("duplicate_key", "duplicate primary key"));
            }
            batch_seen.insert(pk);
        }
        let mut count = 0i64;
        for cells in encoded {
            let bytes = self.encode_row(&t, &cells)?;
            let mut page_id = self.tables[ti].last;
            let mut slot = self.insert_row_into_page(page_id, &bytes);
            while slot.is_none() {
                let newp = self.new_page();
                // link previous page
                if let Some(prev) = Some(self.pages.page_mut(page_id)) {
                    write_u32(prev, 4, newp);
                }
                self.tables[ti].last = newp;
                self.flush_catalog_into_page();
                page_id = newp;
                slot = self.insert_row_into_page(page_id, &bytes);
            }
            let pk = match &self.decode_row(&t, &bytes).values[t.pk] {
                Cell::Int(p) => *p,
                _ => 0,
            };
            self.pk_maps[ti].insert(pk, (page_id, slot.unwrap_or(0) as u16));
            if let Some(vcol) = self.tables[ti].vec_index_col {
                let cell = self.decode_row(&t, &bytes).values[vcol].clone();
                if let (Cell::Vec(v), Some(idx)) = (cell, self.vec_idx[ti].as_mut()) {
                    idx.add(pk, &v);
                }
            }
            count += 1;
        }
        self.auto_commit()?;
        Ok(ExecResult::Affected(count))
    }

    fn matches(&self, t: &Table, row: &Row, filter: Option<&Expr>) -> Result<bool> {
        match filter {
            None => Ok(true),
            Some(e) => self.eval_expr(t, row, e),
        }
    }

    fn eval_expr(&self, t: &Table, row: &Row, e: &Expr) -> Result<bool> {
        match e {
            Expr::And(a, b) => Ok(self.eval_expr(t, row, a)? && self.eval_expr(t, row, b)?),
            Expr::Cmp { col, op, value } => {
                let idx = t
                    .cols
                    .iter()
                    .position(|c| c.name.eq_ignore_ascii_case(col))
                    .ok_or_else(|| DbError::new("column_not_found", "no such column"))?;
                let cell = &row.values[idx];
                Ok(compare(cell, *op, value))
            }
        }
    }

    fn delete(&mut self, table: &str, filter: Option<&Expr>) -> Result<ExecResult> {
        let ti = self.table_idx(table)?;
        let t = self.tables[ti].clone();
        let rows = self.scan(&t);
        let mut targets = Vec::new();
        for (page, slot, row) in rows {
            if self.matches(&t, &row, filter)? {
                let pk = match row.values[t.pk] {
                    Cell::Int(p) => p,
                    _ => 0,
                };
                targets.push((page, slot, pk));
            }
        }
        for (_, _, pk) in &targets {
            self.pk_maps[ti].remove(pk);
            if let Some(idx) = self.vec_idx[ti].as_mut() {
                idx.remove(*pk);
            }
        }
        for (page_id, slot, _) in &targets {
            self.record_undo(*page_id);
            let page = self.pages.page_mut(*page_id);
            Db::set_slot_off(page, *slot, 0);
        }
        self.auto_commit()?;
        Ok(ExecResult::Affected(targets.len() as i64))
    }

    fn update(&mut self, table: &str, sets: &[(String, Value)], filter: Option<&Expr>) -> Result<ExecResult> {
        let ti = self.table_idx(table)?;
        let t = self.tables[ti].clone();
        let mut set_cols = Vec::new();
        for (name, v) in sets {
            let idx = t
                .cols
                .iter()
                .position(|c| c.name.eq_ignore_ascii_case(name))
                .ok_or_else(|| DbError::new("column_not_found", "no such column"))?;
            if idx == t.pk {
                return Err(DbError::new("unsupported_sql", "primary key update not supported"));
            }
            set_cols.push((idx, self.coerce(&t, idx, v)?));
        }
        let rows = self.scan(&t);
        let mut targets = Vec::new();
        for (page, slot, row) in rows {
            if self.matches(&t, &row, filter)? {
                targets.push((page, slot, row));
            }
        }
        let affected = targets.len() as i64;
        for (page_id, slot, mut row) in targets {
            for (idx, cell) in &set_cols {
                row.values[*idx] = cell.clone();
            }
            let bytes = self.encode_row(&t, &row.values)?;
            // rewrite in place is not possible if size changes; append new row and repoint slot
            let appended = self.append_on_page(page_id, &bytes);
            let new_off = match appended {
                Some(off) => off,
                None => {
                    let newp = self.new_page();
                    if let Some(prev) = Some(self.pages.page_mut(self.tables[ti].last)) {
                        write_u32(prev, 4, newp);
                    }
                    self.tables[ti].last = newp;
                    self.flush_catalog_into_page();
                    self.append_on_page(newp, &bytes).ok_or_else(|| DbError::new("io_error", "page full"))?
                }
            };
            let page = self.pages.page_mut(page_id);
            Db::set_slot_off(page, slot, new_off);
            self.pages.mark_dirty(page_id);
            if set_cols.iter().any(|(i, _)| Some(*i) == self.tables[ti].vec_index_col) {
                if let Cell::Int(pk) = row.values[t.pk] {
                    if let Some(vcol) = self.tables[ti].vec_index_col {
                        let v = match &row.values[vcol] {
                            Cell::Vec(v) => Some(v.clone()),
                            _ => None,
                        };
                        if let Some(idx) = self.vec_idx[ti].as_mut() {
                            idx.remove(pk);
                            if let Some(v) = v {
                                idx.add(pk, &v);
                            }
                        }
                    }
                }
            }
        }
        self.auto_commit()?;
        Ok(ExecResult::Affected(affected))
    }

    fn append_on_page(&mut self, page_id: u32, bytes: &[u8]) -> Option<usize> {
        self.record_undo(page_id);
        let page = self.pages.page_mut(page_id);
        let free = Db::page_free_start(page);
        if free + bytes.len() > PAGE {
            return None;
        }
        page[free..free + bytes.len()].copy_from_slice(bytes);
        write_u16(page, 2, (free + bytes.len()) as u16);
        Some(free)
    }

    /// 流式 TopK：不物化全表，内存只保留 K 条
    fn stream_topk(
        &mut self,
        t: &Table,
        vcol: usize,
        target: &[f32],
        filter: Option<&Expr>,
        k: usize,
    ) -> Result<Vec<(Row, f32)>> {
        let mut heap: Vec<(f32, i64, Row)> = Vec::with_capacity(k + 1);
        let mut page_id = t.root;
        let pk_idx = t.pk;
        while page_id != 0 {
            let (slots, next) = {
                let page = self.pages.page(page_id);
                (Db::page_slot_count(page), read_u32(page, 4))
            };
            for slot in 0..slots {
                let off = match self.read_row(page_id, slot) {
                    Some(r) => r.off,
                    None => continue,
                };
                let d = match self.dist_at(t, page_id, slot, vcol, target) {
                    Some(d) => d,
                    None => continue,
                };
                let bytes = {
                    let page = self.pages.page(page_id);
                    page[off..].to_vec()
                };
                let row = self.decode_row(t, &bytes);
                if !self.matches(t, &row, filter)? {
                    continue;
                }
                let pk = match row.values[pk_idx] {
                    Cell::Int(p) => p,
                    _ => 0,
                };
                let cmp = |a: &(f32, i64, Row), b: &(f32, i64, Row)| {
                    a.0.partial_cmp(&b.0)
                        .unwrap_or(std::cmp::Ordering::Equal)
                        .then(a.1.cmp(&b.1))
                };
                if heap.len() < k {
                    heap.push((d, pk, row));
                    heap.sort_by(cmp);
                } else {
                    let worse = {
                        let w = &heap[k - 1];
                        w.0.partial_cmp(&d).unwrap_or(std::cmp::Ordering::Equal) == std::cmp::Ordering::Greater
                            || (w.0 == d && w.1 > pk)
                    };
                    if worse {
                        heap.pop();
                        heap.push((d, pk, row));
                        heap.sort_by(cmp);
                    }
                }
            }
            page_id = next;
        }
        Ok(heap.into_iter().map(|(d, _, r)| (r, d)).collect())
    }

    fn select(
        &mut self,
        table: &str,
        items: &[SelectItem],
        filter: Option<&Expr>,
        order: Option<&(String, bool)>,
        limit: Option<usize>,
    ) -> Result<ExecResult> {
        let ti = self.table_idx(table)?;
        let t = self.tables[ti].clone();
        let t = &t;
        let mut kept: Vec<(Row, Option<f64>)> = Vec::new();
        let mut used_ann = false;
        let order_spec: Option<(OrderKey, bool)> = order.map(|(col, asc)| {
            if let Some(idx) = t.cols.iter().position(|c| c.name.eq_ignore_ascii_case(col)) {
                (OrderKey::Column(idx), *asc)
            } else {
                let mut spec = None;
                for item in items {
                    if let SelectItem::L2 { col: vcol, target, alias } = item {
                        if alias.eq_ignore_ascii_case(col) {
                            let idx = t
                                .cols
                                .iter()
                                .position(|c| c.name.eq_ignore_ascii_case(vcol))
                                .unwrap_or(0);
                            let dim = match t.cols[idx].ty {
                                ColType::Vector(d) => d,
                                _ => 0,
                            };
                            let tv = parse_vector(target, dim).unwrap_or_default();
                            spec = Some(OrderKey::L2(idx, tv));
                            break;
                        }
                    }
                }
                (spec.unwrap_or(OrderKey::Column(0)), *asc)
            }
        });
        let pk_idx = t.pk;
        // ANN：先用 IVF 剪枝取候选；候选不足时退回精确扫描
        if self.search_mode == "ann" {
            if let (Some((OrderKey::L2(vcol, target), _)), Some(lim)) = (order_spec.as_ref(), limit) {
                const ANN_MIN_ROWS: usize = 2_000;
                if self.vec_idx[ti].is_some() && lim > 0 && self.pk_maps[ti].len() > ANN_MIN_ROWS {
                    let hits = self.ann_topk(ti, target, (lim.max(10) * 2) as usize);
                    if !hits.is_empty() {
                        let locs: Vec<(u32, u16)> = hits.iter().map(|(_, _, l)| *l).collect();
                        let dists: Vec<f64> = hits.iter().map(|(d, _, _)| *d as f64).collect();
                        let mut ann_rows: Vec<(Row, Option<f64>)> = Vec::new();
                        for (k, (_, _, row)) in self.read_rows_at(t, &locs).into_iter().enumerate() {
                            if self.matches(t, &row, filter)? {
                                ann_rows.push((row, Some(dists[k])));
                            }
                        }
                        if ann_rows.len() >= lim {
                            kept = ann_rows;
                            used_ann = true;
                        }
                    }
                    let _ = vcol;
                }
            }
        }
        // 精确 TopK 走流式扫描：内存只保留 K 条
        if self.search_mode != "ann" {
            if let (Some((OrderKey::L2(vcol, target), _)), Some(lim)) = (order_spec.as_ref(), limit) {
                if lim > 0 {
                    let hits = self.stream_topk(t, *vcol, &target[..], filter, lim)?;
                    for (row, d) in hits {
                        kept.push((row, Some(d as f64)));
                    }
                    used_ann = true; // 复用"已取到结果，不再全表扫"的语义
                }
            }
        }
        if !used_ann {
            for (page, slot, row) in self.scan(t) {
                if !self.matches(t, &row, filter)? {
                    continue;
                }
                let dist = match order_spec.as_ref() {
                    Some((OrderKey::L2(vcol, target), _)) => {
                        self.dist_at(t, page, slot, *vcol, target).map(|d| d as f64)
                    }
                    _ => None,
                };
                kept.push((row, dist));
            }
        }
        // build output columns
        let mut columns: Vec<String> = Vec::new();
        let mut plan: Vec<PlanItem> = Vec::new();
        for item in items {
            match item {
                SelectItem::Star => {
                    for (i, c) in t.cols.iter().enumerate() {
                        columns.push(c.name.clone());
                        plan.push(PlanItem::Col(i));
                    }
                }
                SelectItem::Col(name) => {
                    let idx = t
                        .cols
                        .iter()
                        .position(|c| c.name.eq_ignore_ascii_case(name))
                        .ok_or_else(|| DbError::new("column_not_found", "no such column"))?;
                    columns.push(t.cols[idx].name.clone());
                    plan.push(PlanItem::Col(idx));
                }
                SelectItem::L2 { col, target, alias } => {
                    let idx = t
                        .cols
                        .iter()
                        .position(|c| c.name.eq_ignore_ascii_case(col))
                        .ok_or_else(|| DbError::new("column_not_found", "no such column"))?;
                    let dim = match t.cols[idx].ty {
                        ColType::Vector(d) => d,
                        _ => return Err(DbError::new("type_mismatch", "L2_DISTANCE needs a vector column")),
                    };
                    let tv = parse_vector(target, dim)?;
                    columns.push(alias.clone());
                    plan.push(PlanItem::L2(idx, tv));
                }
            }
        }
        // ordering
        // resolve ORDER BY key: table column or L2 alias
        let mut keyed: Vec<(Option<SortKey>, i64, Row)> = kept
            .into_iter()
            .map(|(row, dist)| {
                let key = order_spec.as_ref().map(|(spec, _)| match spec {
                    OrderKey::Column(idx) => sort_key(&row.values[*idx]),
                    OrderKey::L2(_, _) => SortKey::Float(dist.unwrap_or(f64::INFINITY)),
                });
                let pk = match row.values[pk_idx] {
                    Cell::Int(i) => i,
                    _ => 0,
                };
                (key, pk, row)
            })
            .collect();
        if let Some((_, asc)) = order_spec.as_ref() {
            let asc = *asc;
            keyed.sort_by(|a, b| {
                let ord = cmp_keys(a.0.as_ref().unwrap(), b.0.as_ref().unwrap());
                let ord = if asc { ord } else { ord.reverse() };
                // ties always break by primary key ascending
                if ord == std::cmp::Ordering::Equal {
                    a.1.cmp(&b.1)
                } else {
                    ord
                }
            });
        }
        let mut out_rows: Vec<Vec<json::Json>> = Vec::new();
        for (_, _, row) in keyed.into_iter() {
            if let Some(l) = limit {
                if out_rows.len() >= l {
                    break;
                }
            }
            let mut cells = Vec::with_capacity(plan.len());
            for p in &plan {
                match p {
                    PlanItem::Col(i) => cells.push(row.values[*i].to_json()),
                    PlanItem::L2(i, target) => {
                        let d = match &row.values[*i] {
                            Cell::Vec(v) => l2(v, target),
                            _ => f32::NAN,
                        };
                        cells.push(json::Json::Float(d as f64));
                    }
                }
            }
            out_rows.push(cells);
        }
        Ok(ExecResult::Rows(columns, out_rows))
    }
}

struct RowRef {
    page: u32,
    off: usize,
}

enum PlanItem {
    Col(usize),
    L2(usize, Vec<f32>),
}

enum OrderKey {
    Column(usize),
    L2(usize, Vec<f32>),
}

pub enum ExecResult {
    Ok,
    Affected(i64),
    Rows(Vec<String>, Vec<Vec<json::Json>>),
}

#[derive(PartialEq)]
enum SortKey {
    Int(i64),
    Str(String),
    Float(f64),
}

fn sort_key(c: &Cell) -> SortKey {
    match c {
        Cell::Int(i) => SortKey::Int(*i),
        Cell::Str(s) => SortKey::Str(s.clone()),
        Cell::Vec(v) => SortKey::Float(v.first().copied().unwrap_or(0.0) as f64),
        Cell::Null => SortKey::Int(0),
    }
}

fn cmp_keys(a: &SortKey, b: &SortKey) -> std::cmp::Ordering {
    match (a, b) {
        (SortKey::Int(x), SortKey::Int(y)) => x.cmp(y),
        (SortKey::Str(x), SortKey::Str(y)) => x.cmp(y),
        (SortKey::Float(x), SortKey::Float(y)) => x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal),
        _ => std::cmp::Ordering::Equal,
    }
}

fn compare(cell: &Cell, op: CmpOp, v: &Value) -> bool {
    match (cell, v) {
        (Cell::Int(a), Value::Int(b)) => match op {
            CmpOp::Eq => a == b,
            CmpOp::Lt => a < b,
            CmpOp::Le => a <= b,
            CmpOp::Gt => a > b,
            CmpOp::Ge => a >= b,
        },
        (Cell::Str(a), Value::Str(b)) => match op {
            CmpOp::Eq => a == b,
            CmpOp::Lt => a < b,
            CmpOp::Le => a <= b,
            CmpOp::Gt => a > b,
            CmpOp::Ge => a >= b,
        },
        _ => false,
    }
}

pub fn parse_vector(s: &str, dim: usize) -> Result<Vec<f32>> {
    let text = s.trim();
    if !text.starts_with('[') || !text.ends_with(']') {
        return Err(DbError::new("invalid_vector", "vector literal must be [..]"));
    }
    let inner = &text[1..text.len() - 1];
    let mut out = Vec::new();
    if !inner.trim().is_empty() {
        for part in inner.split(',') {
            let p = part.trim();
            if p.is_empty() {
                return Err(DbError::new("invalid_vector", "empty component"));
            }
            let f: f64 = p.parse().map_err(|_| DbError::new("invalid_vector", "bad number"))?;
            if !f.is_finite() {
                return Err(DbError::new("invalid_vector", "non-finite component"));
            }
            let f32v = f as f32;
            if !f32v.is_finite() {
                return Err(DbError::new("invalid_vector", "component overflow"));
            }
            out.push(f32v);
        }
    }
    if out.len() != dim {
        return Err(DbError::new("dimension_mismatch", "vector dimension mismatch"));
    }
    Ok(out)
}

pub fn l2(a: &[f32], b: &[f32]) -> f32 {
    let mut sum = 0f32;
    for i in 0..a.len().min(b.len()) {
        let d = a[i] - b[i];
        sum += d * d;
    }
    sum.sqrt()
}

fn build_ivf(points: Vec<(i64, Vec<f32>)>, dim: usize) -> VecIndex {
    let n = points.len();
    if n == 0 {
        return VecIndex { dim, centroids: Vec::new(), radius: Vec::new(), members: Vec::new() };
    }
    let mut k = (n as f64).sqrt() as usize;
    k = k.clamp(1, 256).min(n);
    let mut centroids: Vec<Vec<f32>> = (0..k)
        .map(|i| points[i * n / k].1.clone())
        .collect();
    for _ in 0..6 {
        let mut sums = vec![vec![0f64; dim]; k];
        let mut counts = vec![0usize; k];
        for (_, v) in points.iter() {
            let mut best = 0;
            let mut best_d = f32::INFINITY;
            for (ci, c) in centroids.iter().enumerate() {
                let d = l2(v, c);
                if d < best_d {
                    best_d = d;
                    best = ci;
                }
            }
            counts[best] += 1;
            for j in 0..dim.min(v.len()) {
                sums[best][j] += v[j] as f64;
            }
        }
        for ci in 0..k {
            if counts[ci] == 0 {
                continue;
            }
            for j in 0..dim {
                centroids[ci][j] = (sums[ci][j] / counts[ci] as f64) as f32;
            }
        }
    }
    let mut members = vec![Vec::new(); k];
    let mut radius = vec![0f32; k];
    for (pk, v) in points {
        let mut best = 0;
        let mut best_d = f32::INFINITY;
        for (ci, c) in centroids.iter().enumerate() {
            let d = l2(&v, c);
            if d < best_d {
                best_d = d;
                best = ci;
            }
        }
        members[best].push(pk);
        let d = l2(&v, &centroids[best]);
        if d > radius[best] {
            radius[best] = d;
        }
    }
    VecIndex { dim, centroids, radius, members }
}
