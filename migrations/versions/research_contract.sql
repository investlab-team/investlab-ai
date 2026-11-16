-- ERD v1.1 research boundary. No cross-database references or extensions.
CREATE TABLE companies (
 id UUID PRIMARY KEY, corp_code VARCHAR(32) NOT NULL UNIQUE, name TEXT NOT NULL, profile JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE disclosure_documents (
 id UUID PRIMARY KEY, company_id UUID NOT NULL REFERENCES companies(id), provider TEXT NOT NULL,
 external_id TEXT NOT NULL, version INT NOT NULL CHECK (version > 0), title TEXT NOT NULL,
 document_type VARCHAR(10) NOT NULL CHECK (document_type IN ('ANNUAL','HALF','Q1','Q3','MAJOR')),
 published_at TIMESTAMPTZ NOT NULL, fiscal_year INT, source_url TEXT NOT NULL,
 raw_object_key TEXT NOT NULL, content_hash TEXT NOT NULL, supersedes_id UUID REFERENCES disclosure_documents(id),
 status VARCHAR(12) NOT NULL CHECK (status IN ('COLLECTED','READY','FAILED')),
 attempt_count INT NOT NULL DEFAULT 0, last_error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(provider,external_id,version)
);
CREATE INDEX disclosure_documents_company_published_idx ON disclosure_documents(company_id,published_at);
CREATE TABLE ingestion_jobs (
 id UUID PRIMARY KEY, kind VARCHAR(15) NOT NULL CHECK (kind IN ('DISCLOSURE','FINANCIAL','REINDEX')),
 company_id UUID REFERENCES companies(id), parameters JSONB NOT NULL,
 status VARCHAR(12) NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
 stage TEXT NOT NULL, attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
 success_count INT NOT NULL DEFAULT 0 CHECK (success_count >= 0),
 failure_count INT NOT NULL DEFAULT 0 CHECK (failure_count >= 0), last_error_code TEXT,
 started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE financial_statements (
 id UUID PRIMARY KEY, company_id UUID NOT NULL REFERENCES companies(id),
 document_id UUID REFERENCES disclosure_documents(id), fiscal_year INT NOT NULL,
 period VARCHAR(3) NOT NULL CHECK (period IN ('FY','Q1','H1','Q3')),
 scope VARCHAR(15) NOT NULL CHECK (scope IN ('CONSOLIDATED','SEPARATE')),
 currency CHAR(3) NOT NULL, unit_scale INT NOT NULL CHECK (unit_scale > 0), provider TEXT NOT NULL,
 external_id TEXT NOT NULL, revision INT NOT NULL CHECK (revision > 0), published_at TIMESTAMPTZ NOT NULL,
 source_url TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(company_id,fiscal_year,period,scope,provider,revision)
);
CREATE TABLE financial_facts (
 id UUID PRIMARY KEY, statement_id UUID NOT NULL REFERENCES financial_statements(id),
 statement_type VARCHAR(3) NOT NULL CHECK (statement_type IN ('BS','IS','CIS','CF','SCE')),
 raw_account_code TEXT NOT NULL, raw_name TEXT NOT NULL, standard_code TEXT, mapping_version INT,
 value NUMERIC(28,8), status VARCHAR(10) NOT NULL CHECK (status IN ('AVAILABLE','MISSING','UNMAPPED')),
 original_value TEXT, original_unit TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(statement_id,statement_type,raw_account_code),
 CHECK ((status = 'UNMAPPED') = (standard_code IS NULL))
);
CREATE UNIQUE INDEX financial_facts_standard_uq ON financial_facts(statement_id,standard_code) WHERE standard_code IS NOT NULL;
CREATE TABLE financial_account_mappings (
 id UUID PRIMARY KEY, provider TEXT NOT NULL, raw_account_code TEXT NOT NULL, standard_code TEXT NOT NULL,
 mapping_version INT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(provider,raw_account_code,mapping_version)
);
CREATE TABLE document_chunks (
 id UUID PRIMARY KEY, document_id UUID NOT NULL REFERENCES disclosure_documents(id), chunk_index INT NOT NULL,
 content TEXT NOT NULL, section_path TEXT, page_number INT, start_offset INT, end_offset INT,
 content_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(document_id,chunk_index)
);
COMMENT ON TABLE document_chunks IS 'Optional relational projection. Current RAG uses verified per-document chunks.json.gz in private object storage to stay within free DB capacity.';
CREATE TABLE embedding_records (
 id UUID PRIMARY KEY, chunk_id UUID NOT NULL REFERENCES document_chunks(id), model TEXT NOT NULL,
 model_version TEXT NOT NULL, dimension INT NOT NULL CHECK (dimension > 0), index_name TEXT NOT NULL,
 vector_key TEXT NOT NULL, status VARCHAR(8) NOT NULL CHECK (status IN ('PENDING','READY','FAILED')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(chunk_id,model,model_version), UNIQUE(index_name,vector_key)
);

-- Stable public company UUID, identical on every machine and Neon branch.
-- Exposed by companies.id; clients must not invent a second corp-code mapping.
CREATE FUNCTION investlab_company_id(code TEXT) RETURNS UUID LANGUAGE sql IMMUTABLE STRICT AS
 $$ SELECT md5('opendart:company:' || code)::uuid $$;
CREATE FUNCTION sync_rag_company_contract() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO companies(id,corp_code,name,profile)
 VALUES(investlab_company_id(NEW.corp_code),NEW.corp_code,NEW.data->>'corp_name',NEW.data)
 ON CONFLICT(corp_code) DO UPDATE SET name=EXCLUDED.name,profile=EXCLUDED.profile,updated_at=now();
 RETURN NEW;
END $$;
CREATE TRIGGER rag_company_contract AFTER INSERT OR UPDATE ON rag_companies
 FOR EACH ROW EXECUTE FUNCTION sync_rag_company_contract();
INSERT INTO companies(id,corp_code,name,profile)
 SELECT investlab_company_id(corp_code),corp_code,data->>'corp_name',data FROM rag_companies;

CREATE FUNCTION sync_rag_document_contract() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE company UUID; report_type TEXT; title TEXT;
BEGIN
 company := investlab_company_id(NEW.disclosure->>'corp_code');
 INSERT INTO companies(id,corp_code,name) VALUES(company,NEW.disclosure->>'corp_code',NEW.disclosure->>'corp_name')
 ON CONFLICT(corp_code) DO NOTHING;
 title := NEW.disclosure->>'report_name';
 report_type := CASE WHEN title LIKE '%사업보고서%' THEN 'ANNUAL'
  WHEN title LIKE '%반기보고서%' THEN 'HALF' WHEN title LIKE '%분기보고서%' AND title LIKE '%.03)%' THEN 'Q1'
  WHEN title LIKE '%분기보고서%' AND title LIKE '%.09)%' THEN 'Q3' ELSE 'MAJOR' END;
 INSERT INTO disclosure_documents(id,company_id,provider,external_id,version,title,document_type,
  published_at,fiscal_year,source_url,raw_object_key,content_hash,status,created_at,updated_at)
 VALUES(NEW.document_id::uuid,company,'DART',NEW.rcept_no,1,title,report_type,
  (NEW.disclosure->>'filed_on')::date::timestamp AT TIME ZONE 'Asia/Seoul',
  substring(title FROM '\(([0-9]{4})\.[0-9]{2}\)')::int,
  NEW.disclosure->>'source_url',coalesce(NEW.original_object_key,'postgresql:rag_documents/'||NEW.rcept_no),
  NEW.content_hash,CASE WHEN NEW.chunk_count > 0 THEN 'READY' ELSE 'COLLECTED' END,NEW.collected_at,now())
 ON CONFLICT(id) DO UPDATE SET title=EXCLUDED.title,document_type=EXCLUDED.document_type,
  raw_object_key=EXCLUDED.raw_object_key,content_hash=EXCLUDED.content_hash,status=EXCLUDED.status,updated_at=now();
 RETURN NEW;
END $$;
CREATE TRIGGER rag_document_contract AFTER INSERT OR UPDATE ON rag_documents
 FOR EACH ROW EXECUTE FUNCTION sync_rag_document_contract();
-- Fires adapter once for already collected local data, without rewriting original ZIPs.
UPDATE rag_documents SET chunk_count=chunk_count;
CREATE VIEW rag_evidence_manifest AS SELECT document_id::uuid AS document_id,rcept_no,
 original_object_key,chunks_object_key,content_hash,chunk_count,embedding_model,embedding_dimensions,parser_version
 FROM rag_documents;
