Feature: Boto3 S3 transport
  The concrete S3 transport keeps credentials at the client boundary.

  Scenario: The Boto3 transport replays an exact object
    Given a fake Boto3 client and secret resolver
    When I put and read an object through the Boto3 transport
    Then the transport uses the configured bucket and secret reference

  Scenario: The Boto3 transport maps missing objects safely
    Given a fake Boto3 client and secret resolver
    When I read a missing object through the Boto3 transport
    Then the missing object is reported without provider details

  Scenario: The Boto3 transport lists all paginated keys
    Given a fake Boto3 client and secret resolver
    When I list objects through the Boto3 transport
    Then all matching keys are returned in provider order
