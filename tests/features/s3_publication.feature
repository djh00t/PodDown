Feature: Publish packages to immutable S3 object storage
  Scenario: Publish a verified package through the S3 object-store adapter
    Given a verified package and an S3 publishing target
    When I publish the package through S3 object storage
    Then the receipt remains publication-contract compatible and S3 has verified bytes
