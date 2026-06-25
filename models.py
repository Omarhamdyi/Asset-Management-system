from typing import List

from sqlalchemy import ARRAY, CheckConstraint, Column, DateTime, Enum, ForeignKeyConstraint, Index, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship
from sqlalchemy.orm.base import Mapped

Base = declarative_base()


class Organizations(Base):
    __tablename__ = 'organizations'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='organizations_pkey'),
        UniqueConstraint('slug', name='organizations_slug_key')
    )

    id = mapped_column(Uuid, server_default=text('gen_random_uuid()'))
    slug = mapped_column(Text, nullable=False)
    name = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    asset_import_batches: Mapped[List['AssetImportBatches']] = relationship('AssetImportBatches', uselist=True, back_populates='organization')
    assets: Mapped[List['Assets']] = relationship('Assets', uselist=True, back_populates='organization')
    asset_relationships: Mapped[List['AssetRelationships']] = relationship('AssetRelationships', uselist=True, back_populates='organization')


class AssetImportBatches(Base):
    __tablename__ = 'asset_import_batches'
    __table_args__ = (
        CheckConstraint('total_records >= 0 AND successful_records >= 0 AND failed_records >= 0', name='asset_import_batches_counts'),
        ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE', name='asset_import_batches_organization_id_fkey'),
        PrimaryKeyConstraint('id', name='asset_import_batches_pkey')
    )

    id = mapped_column(Uuid, server_default=text('gen_random_uuid()'))
    organization_id = mapped_column(Uuid, nullable=False)
    source_name = mapped_column(Text, nullable=False)
    status = mapped_column(Enum('pending', 'processing', 'completed', 'completed_with_errors', 'failed', name='import_batch_status'), nullable=False, server_default=text("'pending'::import_batch_status"))
    total_records = mapped_column(Integer, nullable=False, server_default=text('0'))
    successful_records = mapped_column(Integer, nullable=False, server_default=text('0'))
    failed_records = mapped_column(Integer, nullable=False, server_default=text('0'))
    record_errors = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    relationship_errors = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    started_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    created_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    source_checksum = mapped_column(Text)
    completed_at = mapped_column(DateTime(True))

    organization: Mapped['Organizations'] = relationship('Organizations', back_populates='asset_import_batches')


class Assets(Base):
    __tablename__ = 'assets'
    __table_args__ = (
        CheckConstraint('length(btrim(normalized_value)) > 0', name='assets_normalized_value_nonempty'),
        CheckConstraint('length(btrim(value)) > 0', name='assets_value_nonempty'),
        ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE', name='assets_organization_id_fkey'),
        PrimaryKeyConstraint('id', name='assets_pkey'),
        UniqueConstraint('organization_id', 'asset_type', 'normalized_value', name='assets_identity_unique'),
        Index('idx_assets_certificate_expires_at', 'organization_id', 'certificate_expires_at'),
        Index('idx_assets_last_seen', 'organization_id', 'last_seen'),
        Index('idx_assets_metadata_gin', 'metadata'),
        Index('idx_assets_organization_normalized_value', 'organization_id', 'normalized_value'),
        Index('idx_assets_organization_type_status', 'organization_id', 'asset_type', 'status'),
        Index('idx_assets_tags_gin', 'tags'),
        Index('idx_assets_value_trgm', 'value')
    )

    id = mapped_column(Uuid, server_default=text('gen_random_uuid()'))
    organization_id = mapped_column(Uuid, nullable=False)
    asset_type = mapped_column(Enum('domain', 'subdomain', 'ip_address', 'service', 'certificate', 'technology', name='asset_type'), nullable=False)
    value = mapped_column(Text, nullable=False)
    normalized_value = mapped_column(Text, nullable=False)
    status = mapped_column(Enum('active', 'stale', 'archived', name='asset_status'), nullable=False, server_default=text("'active'::asset_status"))
    source = mapped_column(Enum('import', 'scan', 'manual', name='asset_source'), nullable=False)
    tags = mapped_column(ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]"))
    metadata_ = mapped_column('metadata', JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    first_seen = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    last_seen = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    created_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    certificate_expires_at = mapped_column(DateTime(True))

    organization: Mapped['Organizations'] = relationship('Organizations', back_populates='assets')
    asset_relationships: Mapped[List['AssetRelationships']] = relationship('AssetRelationships', uselist=True, foreign_keys='[AssetRelationships.source_asset_id]', back_populates='source_asset')
    asset_relationships_: Mapped[List['AssetRelationships']] = relationship('AssetRelationships', uselist=True, foreign_keys='[AssetRelationships.target_asset_id]', back_populates='target_asset')


class AssetRelationships(Base):
    __tablename__ = 'asset_relationships'
    __table_args__ = (
        CheckConstraint("relationship_type = 'subdomain_to_domain'::asset_relationship_type AND source_asset_type = 'subdomain'::asset_type AND target_asset_type = 'domain'::asset_type OR relationship_type = 'service_to_ip_address'::asset_relationship_type AND source_asset_type = 'service'::asset_type AND target_asset_type = 'ip_address'::asset_type OR relationship_type = 'ip_address_to_subdomain'::asset_relationship_type AND source_asset_type = 'ip_address'::asset_type AND target_asset_type = 'subdomain'::asset_type OR relationship_type = 'certificate_to_domain'::asset_relationship_type AND source_asset_type = 'certificate'::asset_type AND target_asset_type = 'domain'::asset_type OR relationship_type = 'certificate_to_subdomain'::asset_relationship_type AND source_asset_type = 'certificate'::asset_type AND target_asset_type = 'subdomain'::asset_type OR relationship_type = 'technology_to_subdomain'::asset_relationship_type AND source_asset_type = 'technology'::asset_type AND target_asset_type = 'subdomain'::asset_type OR relationship_type = 'technology_to_service'::asset_relationship_type AND source_asset_type = 'technology'::asset_type AND target_asset_type = 'service'::asset_type", name='asset_relationships_shape'),
        CheckConstraint('source_asset_id <> target_asset_id', name='asset_relationships_not_self'),
        ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE', name='asset_relationships_organization_id_fkey'),
        ForeignKeyConstraint(['source_asset_id'], ['assets.id'], ondelete='CASCADE', name='asset_relationships_source_asset_id_fkey'),
        ForeignKeyConstraint(['target_asset_id'], ['assets.id'], ondelete='CASCADE', name='asset_relationships_target_asset_id_fkey'),
        PrimaryKeyConstraint('id', name='asset_relationships_pkey'),
        UniqueConstraint('organization_id', 'source_asset_id', 'target_asset_id', 'relationship_type', name='asset_relationships_unique'),
        Index('idx_asset_relationships_source', 'organization_id', 'source_asset_id'),
        Index('idx_asset_relationships_target', 'organization_id', 'target_asset_id'),
        Index('idx_asset_relationships_type', 'organization_id', 'relationship_type')
    )

    id = mapped_column(Uuid, server_default=text('gen_random_uuid()'))
    organization_id = mapped_column(Uuid, nullable=False)
    source_asset_id = mapped_column(Uuid, nullable=False)
    source_asset_type = mapped_column(Enum('domain', 'subdomain', 'ip_address', 'service', 'certificate', 'technology', name='asset_type'), nullable=False)
    target_asset_id = mapped_column(Uuid, nullable=False)
    target_asset_type = mapped_column(Enum('domain', 'subdomain', 'ip_address', 'service', 'certificate', 'technology', name='asset_type'), nullable=False)
    relationship_type = mapped_column(Enum('subdomain_to_domain', 'service_to_ip_address', 'ip_address_to_subdomain', 'certificate_to_domain', 'certificate_to_subdomain', 'technology_to_subdomain', 'technology_to_service', name='asset_relationship_type'), nullable=False)
    metadata_ = mapped_column('metadata', JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    first_seen = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    last_seen = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    created_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    organization: Mapped['Organizations'] = relationship('Organizations', back_populates='asset_relationships')
    source_asset: Mapped['Assets'] = relationship('Assets', foreign_keys=[source_asset_id], back_populates='asset_relationships')
    target_asset: Mapped['Assets'] = relationship('Assets', foreign_keys=[target_asset_id], back_populates='asset_relationships_')
