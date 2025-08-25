import os
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# Load environment variables from .env if present
load_dotenv()

# Database configuration via environment variables
# The notes_backend depends on notes_db container. Expect a URL like:
# postgresql+psycopg://user:pass@host:port/db
# or mysql+pymysql://user:pass@host:port/db
# or sqlite:///./notes.db as a fallback
DB_URL = os.getenv("NOTES_DB_URL") or os.getenv("DATABASE_URL") or "sqlite:///./notes.db"

# SQLAlchemy setup
engine = create_engine(
    DB_URL,
    future=True,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


class NoteORM(Base):
    """SQLAlchemy ORM model for notes table."""
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    tags = Column(String(512), nullable=True)  # Comma-separated tags
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


# Create tables if they do not exist
Base.metadata.create_all(bind=engine)

# FastAPI app with metadata and tags for OpenAPI
openapi_tags = [
    {"name": "health", "description": "Health and status endpoints."},
    {"name": "notes", "description": "CRUD operations for notes."},
]

app = FastAPI(
    title="Notes Backend API",
    description="FastAPI service for personal notes management with CRUD endpoints.",
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# CORS configuration - allow origins via env or default to *
allowed_origins = [o.strip() for o in (os.getenv("CORS_ALLOW_ORIGINS") or "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency to provide DB session
def get_db():
    """Provide a scoped SQLAlchemy session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Pydantic models for request/response
class NoteBase(BaseModel):
    title: str = Field(..., description="Title of the note", min_length=1, max_length=255)
    content: str = Field("", description="Content/body of the note")
    tags: Optional[List[str]] = Field(default=None, description="List of tags for the note")

    def to_tags_str(self) -> Optional[str]:
        return ",".join(self.tags) if self.tags is not None else None

    @staticmethod
    def from_tags_str(tags_str: Optional[str]) -> Optional[List[str]]:
        if tags_str is None or tags_str == "":
            return None
        return [t for t in (s.strip() for s in tags_str.split(",")) if t]


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, description="New title", min_length=1, max_length=255)
    content: Optional[str] = Field(None, description="New content")
    tags: Optional[List[str]] = Field(default=None, description="New tags list (replaces existing)")


class NoteOut(NoteBase):
    id: int = Field(..., description="Unique identifier of the note")
    # created_at and updated_at as isoformat strings
    created_at: Optional[str] = Field(None, description="Creation timestamp (ISO 8601)")
    updated_at: Optional[str] = Field(None, description="Last update timestamp (ISO 8601)")

    @classmethod
    def from_orm_note(cls, orm: NoteORM) -> "NoteOut":
        return cls(
            id=orm.id,
            title=orm.title,
            content=orm.content or "",
            tags=NoteBase.from_tags_str(orm.tags),
            created_at=orm.created_at.isoformat() if orm.created_at else None,
            updated_at=orm.updated_at.isoformat() if orm.updated_at else None,
        )


# PUBLIC_INTERFACE
@app.get("/", tags=["health"], summary="Health Check")
def health_check():
    """Health check endpoint that returns a simple JSON payload."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.post(
    "/api/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
    tags=["notes"],
    summary="Create note",
    responses={
        201: {"description": "Note created successfully"},
        400: {"description": "Invalid input"},
        500: {"description": "Server error"},
    },
)
def create_note(payload: NoteCreate, db: Session = Depends(get_db)) -> NoteOut:
    """
    Create a new note.

    Parameters:
    - payload: NoteCreate body with title, content, and tags.
    - db: Injected database session.

    Returns: NoteOut with created note details.
    """
    try:
        note = NoteORM(
            title=payload.title,
            content=payload.content or "",
            tags=payload.to_tags_str(),
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return NoteOut.from_orm_note(note)
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Integrity error: {str(e.orig)}")
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# PUBLIC_INTERFACE
@app.get(
    "/api/notes",
    response_model=List[NoteOut],
    tags=["notes"],
    summary="List notes",
    responses={200: {"description": "List of notes"}},
)
def list_notes(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(default=None, description="Search by title/content substring"),
    tag: Optional[str] = Query(default=None, description="Filter by a single tag"),
    skip: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=200, description="Pagination limit"),
) -> List[NoteOut]:
    """
    List notes with optional search, tag filtering, and pagination.
    """
    query = db.query(NoteORM)
    if q:
        # simple case-insensitive like filter on title or content
        like = f"%{q}%"
        query = query.filter((NoteORM.title.ilike(like)) | (NoteORM.content.ilike(like)))
    if tag:
        like_tag = f"%{tag}%"
        # naive contains; for accurate tag filter consider delimiter boundaries
        query = query.filter(NoteORM.tags.ilike(like_tag))
    rows = query.order_by(NoteORM.updated_at.desc()).offset(skip).limit(limit).all()
    return [NoteOut.from_orm_note(r) for r in rows]


# PUBLIC_INTERFACE
@app.get(
    "/api/notes/{note_id}",
    response_model=NoteOut,
    tags=["notes"],
    summary="Get note by ID",
    responses={
        200: {"description": "Note found"},
        404: {"description": "Note not found"},
    },
)
def get_note(
    note_id: int = Path(..., ge=1, description="Note ID"),
    db: Session = Depends(get_db),
) -> NoteOut:
    """
    Retrieve a single note by its ID.
    """
    note = db.get(NoteORM, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return NoteOut.from_orm_note(note)


# PUBLIC_INTERFACE
@app.put(
    "/api/notes/{note_id}",
    response_model=NoteOut,
    tags=["notes"],
    summary="Update note (replace fields)",
    responses={
        200: {"description": "Note updated"},
        400: {"description": "Invalid input"},
        404: {"description": "Note not found"},
        500: {"description": "Server error"},
    },
)
def update_note(
    note_id: int = Path(..., ge=1, description="Note ID"),
    payload: NoteUpdate = ...,
    db: Session = Depends(get_db),
) -> NoteOut:
    """
    Update a note. Only provided fields are updated; others remain unchanged.
    """
    note = db.get(NoteORM, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    try:
        if payload.title is not None:
            if len(payload.title) == 0:
                raise HTTPException(status_code=400, detail="Title cannot be empty")
            note.title = payload.title
        if payload.content is not None:
            note.content = payload.content
        if payload.tags is not None:
            note.tags = ",".join(payload.tags)
        db.add(note)
        db.commit()
        db.refresh(note)
        return NoteOut.from_orm_note(note)
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Integrity error: {str(e.orig)}")
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# PUBLIC_INTERFACE
@app.delete(
    "/api/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["notes"],
    summary="Delete note",
    responses={
        204: {"description": "Note deleted"},
        404: {"description": "Note not found"},
        500: {"description": "Server error"},
    },
)
def delete_note(
    note_id: int = Path(..., ge=1, description="Note ID"),
    db: Session = Depends(get_db),
):
    """
    Delete a note by its ID.
    """
    note = db.get(NoteORM, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    try:
        db.delete(note)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    return None
